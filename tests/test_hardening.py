"""Phase 7 acceptance checks: damaged files, USB drives, large sessions, updates, and remaining gaps."""
import io
import os
import random
import sqlite3
import time

import pandas as pd
import pytest
from docx import Document
from PIL import Image

from app.docbuild.standard import ISO_2011, ISO_2023
from app.parts import generators as g
from app.session import Session, SessionDamaged, SessionError, snapshot
from app.session import portable as portable_module
from app.session.portable import PortableCopy
from app.stats import evaluation
from app.version import VERSION
from tests.test_ai import world  # noqa: F401
from tests.test_app_flow import fill_all


def make_session(path, text="original work"):
    session = Session.create(path)
    session.save_inputs("ch1.scope", {"scope": text})
    session.close()


# ---- damaged files --------------------------------------------------------------------------


def test_truncated_file_is_reported_as_damaged(tmp_path):
    path = tmp_path / "a.rrg"
    session = Session.create(path)
    session.add_attachment("ch1.framework", "file", "x.png", "image/png", os.urandom(300_000))
    session.close()
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])
    with pytest.raises(SessionDamaged):
        Session(path)
    path.unlink()  # the failed open must not keep the file locked


@pytest.mark.parametrize("seed", range(6))
def test_randomly_corrupted_file_never_crashes_the_open(tmp_path, seed):
    path = tmp_path / "a.rrg"
    session = Session.create(path)
    for n in range(30):
        session.save_inputs(f"p{n}", {"text": "x" * 2000})
    session.close()
    data = bytearray(path.read_bytes())
    rng = random.Random(seed)
    for _ in range(200):
        data[rng.randrange(100, len(data))] = rng.randrange(256)
    path.write_bytes(bytes(data))
    try:
        Session(path).close()
    except SessionError:
        pass


@pytest.mark.parametrize("content", [b"", b"this is not a database", b"SQLite format 3\x00" + b"\x00" * 100])
def test_non_reports_are_refused(tmp_path, content):
    path = tmp_path / "a.rrg"
    path.write_bytes(content)
    with pytest.raises(SessionError):
        Session(path).close()


def test_damaged_open_offers_the_safety_copy_and_recovers_it(world, tmp_path):  # noqa: F811
    client, web, _relay = world
    path = tmp_path / "Thesis.rrg"
    make_session(path)
    session = Session(path)
    snapshot(session)
    session.close()
    path.write_bytes(b"ruined")
    result = client.post("/api/session/open", json={"path": str(path)}).json()
    assert not result["ok"] and "safety copy" in result["message"] and len(result["safety"]) == 1
    assert client.post("/api/session/recover", json={"path": result["safety"][0]["path"]}).json()["ok"]
    assert web.state.session.inputs("ch1.scope")["scope"] == "original work"
    assert "safety copy" in client.get("/part/ch1.scope").text


def test_recovery_only_accepts_the_safety_folder(world, tmp_path):  # noqa: F811
    client, _web, _relay = world
    outside = tmp_path / "outside.rrg"
    make_session(outside)
    assert not client.post("/api/session/recover", json={"path": str(outside)}).json()["ok"]


def test_session_from_a_newer_app_is_refused(tmp_path):
    path = tmp_path / "a.rrg"
    make_session(path)
    db = sqlite3.connect(path)
    db.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
    db.commit()
    db.close()
    with pytest.raises(SessionError, match="newer version"):
        Session(path)


def test_new_sessions_record_the_app_version(tmp_path):
    session = Session.create(tmp_path / "a.rrg")
    assert session.get_meta("app_version") == VERSION
    session.close()


def test_storage_failure_is_reported_in_words(world):  # noqa: F811
    client, web, _relay = world
    real = web.state.session.db
    web.state.session.db = _BrokenDatabase(real)
    result = client.post("/api/part/ch1.scope/inputs", json={"scope": "x"})
    web.state.session.db = real
    assert result.status_code == 500 and "free disk space" in result.json()["message"]


class _BrokenDatabase:
    def __init__(self, real):
        self.real = real

    def execute(self, *_args):
        raise sqlite3.OperationalError("database or disk is full")

    def __getattr__(self, name):
        return getattr(self.real, name)


# ---- USB drives -----------------------------------------------------------------------------


@pytest.fixture()
def usb(world, tmp_path, monkeypatch):  # noqa: F811
    """Pretend one folder is a removable drive."""
    drive = tmp_path / "USB"
    drive.mkdir()
    monkeypatch.setattr("app.web.needs_local_copy", lambda path: str(path).startswith(str(drive)))
    return drive


def test_usb_session_is_edited_locally_and_written_back(world, usb):  # noqa: F811
    client, web, _relay = world
    origin = usb / "Group.rrg"
    make_session(origin)
    assert client.post("/api/session/open", json={"path": str(origin)}).json()["ok"]
    assert web.state.session.path != origin and not str(web.state.session.path).startswith(str(usb))
    client.post("/api/part/ch1.scope/inputs", json={"scope": "edited on the computer"})
    assert client.get("/api/session/sync").json()["state"] == "saved"
    copy = Session(origin)
    assert copy.inputs("ch1.scope")["scope"] == "edited on the computer"
    copy.close()
    assert "Working from a drive" in client.get("/part/ch1.scope").text


def test_sync_skips_when_nothing_changed(world, usb):  # noqa: F811
    client, _web, _relay = world
    origin = usb / "Group.rrg"
    make_session(origin)
    client.post("/api/session/open", json={"path": str(origin)})
    client.get("/api/session/sync")
    before = origin.stat().st_mtime_ns
    time.sleep(0.05)
    client.get("/api/session/sync")
    assert origin.stat().st_mtime_ns == before


def test_removed_drive_keeps_working_and_recovers(world, usb):  # noqa: F811
    client, web, _relay = world
    origin = usb / "Group.rrg"
    make_session(origin)
    client.post("/api/session/open", json={"path": str(origin)})
    client.get("/api/session/sync")
    hidden = usb.with_name("USB-unplugged")
    usb.rename(hidden)
    assert client.post("/api/part/ch1.scope/inputs", json={"scope": "typed while unplugged"}).json()["ok"]
    assert client.get("/api/session/sync").json()["state"] == "missing"
    assert "could not reach the drive" in client.post("/api/session/close").json()["go"].replace("%20", " ")
    hidden.rename(usb)
    client.post("/api/session/open", json={"path": str(origin)})
    assert web.state.session.inputs("ch1.scope")["scope"] == "typed while unplugged"
    assert "recovered" in client.get("/part/ch1.scope").text
    assert client.get("/api/session/sync").json()["state"] == "saved"
    copy = Session(origin)
    assert copy.inputs("ch1.scope")["scope"] == "typed while unplugged"
    copy.close()


def test_failed_write_never_damages_the_file_on_the_drive(world, usb, monkeypatch):  # noqa: F811
    client, web, _relay = world
    origin = usb / "Group.rrg"
    make_session(origin)
    client.post("/api/session/open", json={"path": str(origin)})
    client.post("/api/part/ch1.scope/inputs", json={"scope": "newer work"})

    def yanked(*_args):
        raise OSError("drive removed during write")

    monkeypatch.setattr(portable_module.os, "fsync", yanked)
    assert client.get("/api/session/sync").json()["state"] == "failed"
    monkeypatch.undo()
    copy = Session(origin)
    assert copy.inputs("ch1.scope")["scope"] == "original work"
    copy.close()
    assert not list(usb.glob("*.saving"))


def test_changes_on_another_computer_win_and_local_work_is_kept(world, usb, tmp_path):  # noqa: F811
    client, web, _relay = world
    origin = usb / "Group.rrg"
    make_session(origin)
    client.post("/api/session/open", json={"path": str(origin)})
    client.get("/api/session/sync")
    client.post("/api/part/ch1.scope/inputs", json={"scope": "unsaved here"})
    working = PortableCopy(origin)
    web.state.session.close()
    web.state.session = None
    web.state.portable = None
    other = Session(origin)
    other.save_inputs("ch1.scope", {"scope": "edited elsewhere"})
    other.close()
    time.sleep(0.02)
    os.utime(origin)
    client.post("/api/session/open", json={"path": str(origin)})
    assert web.state.session.inputs("ch1.scope")["scope"] == "edited elsewhere"
    assert "kept as" in client.get("/part/ch1.scope").text
    from app.session import data_dir

    kept = list((data_dir() / "recovered").glob("Group unsaved*"))
    assert kept and working.origin == origin
    saved = Session(kept[0])
    assert saved.inputs("ch1.scope")["scope"] == "unsaved here"
    saved.close()


def test_new_report_on_a_drive_reaches_the_drive(world, usb, monkeypatch):  # noqa: F811
    client, web, _relay = world
    target = usb / "New.rrg"
    monkeypatch.setattr("app.web.pick_save", lambda *_a: target)
    assert client.post("/api/session/new").json()["ok"]
    assert target.exists() and web.state.session.path != target


def test_ordinary_drives_are_not_copied(world, tmp_path):  # noqa: F811
    client, web, _relay = world
    path = tmp_path / "Local.rrg"
    make_session(path)
    client.post("/api/session/open", json={"path": str(path)})
    assert web.state.session.path == path and web.state.portable is None
    assert "Working from a drive" not in client.get("/part/ch1.scope").text


# ---- large sessions -------------------------------------------------------------------------


def test_large_session_stays_usable(world, tmp_path):  # noqa: F811
    client, web, _relay = world
    path = tmp_path / "Big.rrg"
    session = Session.create(path)
    picture = io.BytesIO()
    Image.effect_noise((1400, 1000), 90).convert("RGB").save(picture, format="JPEG", quality=92)
    blob = picture.getvalue()
    for n in range(60):
        session.add_attachment("ch4.design.modules", "file", f"s{n}.jpg", "image/jpeg", blob, f"Screen number {n}")
    session.close()
    assert path.stat().st_size > 40_000_000
    started = time.perf_counter()
    client.post("/api/session/open", json={"path": str(path)})
    client.get("/part/ch4.design.modules")
    assert time.perf_counter() - started < 15
    fill_all(client, diagrams=False)
    assert client.post("/api/build", json={"final": False}).json()["ok"]
    assert len(Document(web.state.last_export).inline_shapes) == 60


# ---- update notice --------------------------------------------------------------------------


def status_with(world_tuple, latest, url):  # noqa: ANN001
    client, web, relay = world_tuple
    original = relay.__call__
    web.ai.client.update_available.__func__  # the real method is what is being tested
    health = {"latest_app_version": latest, "download_url": url}
    web.ai.update_url = web.ai.client.update_available(health)
    return web.ai.update_url, client, original


@pytest.mark.parametrize(("latest", "url", "expected"), [
    ("99.0.0", "https://example.test/setup.exe", True),
    (VERSION, "https://example.test/setup.exe", False),
    ("0.1.0", "https://example.test/setup.exe", False),
    ("99.0.0", "", False),
    ("99.0.0", "http://example.test/setup.exe", False),
    ("99.0.0", "file:///C:/evil.exe", False),
])
def test_update_notice_only_for_newer_https_releases(world, latest, url, expected):  # noqa: F811
    assert bool(status_with(world, latest, url)[0]) is expected


def test_no_update_means_nothing_is_opened(world, monkeypatch):  # noqa: F811
    client, web, _relay = world
    opened = []
    monkeypatch.setattr("app.web.webbrowser.open", opened.append)
    web.ai.update_url = ""
    assert not client.post("/api/open-update").json()["ok"] and not opened


# ---- ISO edition, reliability, sources, guide -----------------------------------------------


def set_edition(client, edition):
    client.post("/api/part/setup.options/inputs", json={"stage": "Final report", "iso_edition": edition, "hypothesis": "Yes", "appendices_off": []})


def test_2023_edition_changes_the_table_and_questions(world):  # noqa: F811
    client, web, _relay = world
    set_edition(client, "2023")
    client.post("/api/part/ch3.instrument/generate")
    table = web.state.session.draft("ch3.instrument")["after"][0]
    assert [r[0] for r in table["rows"]] == [n for n, _, _ in ISO_2023] and len(table["rows"]) == 9
    client.post("/api/part/ch1.sop/generate")
    children = next(i for i in web.state.session.draft("ch1.sop")["after"][0]["items"] if i["children"] and "reliability;" in i["children"])["children"]
    assert "interaction capability;" in children and "safety?" in children and "portability?" not in children


def test_switching_edition_keeps_scores_for_renamed_characteristics(world):  # noqa: F811
    client, web, _relay = world
    scores = g.inputs_for(web.state.session, "ch4.acceptability")["scores"]
    assert [r["characteristic"] for r in scores] == [n for n, _, _ in ISO_2011]
    next(r for r in scores if r["characteristic"] == "Usability")["experts"] = "4.62"
    client.post("/api/part/ch4.acceptability/inputs", json={"scores": scores})
    set_edition(client, "2023")
    updated = g.inputs_for(web.state.session, "ch4.acceptability")["scores"]
    assert next(r for r in updated if r["characteristic"] == "Interaction Capability")["experts"] == "4.62"
    assert "Usability" not in [r["characteristic"] for r in updated] and "Safety" in [r["characteristic"] for r in updated]
    client.post("/api/part/ch3.instrument/generate")
    assert "Interaction Capability" in client.get("/part/ch3.instrument").text


def pilot_csv():
    return pd.DataFrame({
        "Timestamp": ["a", "b", "c", "d", "e"], "Name": ["Secret A", "Secret B", "Secret C", "Secret D", "Secret E"],
        "Q1": [1, 2, 3, 4, 5], "Q2": [2, 2, 3, 5, 5], "Q3": [1, 3, 3, 4, 4],
    }).to_csv(index=False).encode("utf-8")


def test_pilot_import_computes_the_known_alpha_and_keeps_no_rows(world, tmp_path):  # noqa: F811
    client, web, _relay = world
    result = client.post("/api/import/pilot", files={"file": ("pilot.csv", pilot_csv())}).json()
    assert result["ok"] and "0.95 from 5 respondents and 3 questions" in result["message"]  # 0.954, checked by hand in test_phase3
    assert web.state.session.inputs("ch3.instrument")["alpha"] == "0.95"
    client.post("/api/part/ch3.instrument/inputs", json={"text": "The questionnaire was validated.", "alpha": "0.95"})
    client.post("/api/part/ch3.instrument/generate")
    assert "Cronbach's alpha of 0.95, which meets" in web.state.session.draft("ch3.instrument")["html"]
    web.state.session.close()
    assert b"Secret A" not in web.state.session.path.read_bytes()
    web.state.session = Session(web.state.session.path)


def test_low_alpha_is_reported_honestly(world):  # noqa: F811
    client, web, _relay = world
    client.post("/api/part/ch3.instrument/inputs", json={"text": "x", "alpha": "0.52"})
    client.post("/api/part/ch3.instrument/generate")
    assert "0.52, which is below the acceptable level" in web.state.session.draft("ch3.instrument")["html"]


@pytest.mark.parametrize("frame", [
    pd.DataFrame({"Name": ["a", "b", "c"], "Q1": [1, 2, 3]}),
    pd.DataFrame({"Q1": [1, 2], "Q2": [2, 3]}),
    pd.DataFrame({"Q1": [3, 3, 3], "Q2": [4, 4, 4]}),
])
def test_unusable_pilot_data_is_refused(frame):
    with pytest.raises(evaluation.ImportError_):
        evaluation.pilot_alpha(frame)


def test_source_quality_prompts(world):  # noqa: F811
    from app.parts.export import run_checks

    client, web, _relay = world
    client.post("/api/part/ch2.sources/inputs", json={"sources": [
        {"authors": "Cruz, A.", "year": "2001", "title": "An old study", "container": "J"},
        {"authors": "", "year": "2024", "title": "Web page", "container": "Wikipedia"},
    ]})
    found = [i for i in run_checks(web.state.session) if i["key"] == "ch2.sources"]
    text = " ".join(i["message"] for i in found)
    assert "at least 15" in text and "Older than 10 years (2001)" in text and "Wikipedia" in text and "Missing author" in text


def test_student_guide_is_reachable_from_launch_and_every_page(world):  # noqa: F811
    client, web, _relay = world
    page = client.get("/guide")
    assert page.status_code == 200 and "You remain responsible for every line" in page.text
    assert 'href="/guide"' in client.get("/part/ch1.scope").text
    session, web.state.session = web.state.session, None
    launch = client.get("/").text
    web.state.session = session
    assert 'href="/guide"' in launch and "delete it when you no longer need it" in launch


def test_trace_log_records_no_student_content(tmp_path, monkeypatch):
    monkeypatch.setenv("RRG_DATA_DIR", str(tmp_path))
    from app.runtime_log import log, start

    before = list(log.handlers)
    excepthook = __import__("sys").excepthook
    thread_hook = __import__("threading").excepthook
    try:
        start()
        log.info("window closing")
        for handler in log.handlers:
            handler.flush()
        text = (tmp_path / "app.log").read_text(encoding="utf-8")
        assert f"version={VERSION}" in text and "window closing" in text
    finally:
        for handler in [h for h in log.handlers if h not in before]:
            handler.close()
            log.removeHandler(handler)
        __import__("sys").excepthook = excepthook
        __import__("threading").excepthook = thread_hook
        import faulthandler

        faulthandler.disable()
