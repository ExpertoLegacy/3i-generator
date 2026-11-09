"""Fill a whole session through the app's own API, using typed input only."""
import io
import os
import socket
import sys
from pathlib import Path

os.environ["RRG_NO_TOKEN"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from docx import Document  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.parts import generators as g  # noqa: E402
from app.parts import spec as spec_module  # noqa: E402
from app.session import Session  # noqa: E402

TEXT = "The researchers recorded this answer for the sample report during the study."


ONE_PER_QUESTION = ("ch1.objectives", "ch5.summary", "ch5.conclusions")


def filled(part: dict, questions: int = 6) -> dict:
    values = g.default_inputs(part)
    for field in part["inputs"]:
        fid = field["id"]
        if part["key"] in ONE_PER_QUESTION and fid in ("objectives", "items"):
            values[fid] = "\n".join(f"Statement {n}." for n in range(1, questions + 1))
        elif field["type"] == "textarea" and not values.get(fid):
            values[fid] = TEXT + "\nA second line of the answer."
        elif field["type"] == "text" and not values.get(fid):
            values[fid] = "Sample value"
        elif field["type"] == "select" and not values.get(fid):
            values[fid] = field["options"][0]
        elif field["type"] == "repeater":
            if field.get("default_factory"):
                continue
            rows = values.get(fid) or [{}, {}, {}]
            for row in rows:
                for sub in field["fields"]:
                    if sub.get("type") != "hidden" and not row.get(sub["id"]):
                        row[sub["id"]] = "Sample entry text"
            values[fid] = rows
        elif field["type"] == "grid":
            rows = values.get(fid) or [{}, {}]
            for row in rows:
                for column in field["columns"]:
                    if column.get("readonly"):
                        continue
                    if not row.get(column["id"]):
                        row[column["id"]] = "4.5" if fid in ("scores", "groups") else "Pass" if column["id"] == "status" else "Sample"
            values[fid] = rows
    return values


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("RRG_DATA_DIR", str(tmp_path / "data"))
    from app import web

    web.state.session = Session.create(tmp_path / "group.rrg")
    web.state.last_export = None
    with TestClient(web.app) as client:
        yield client, web, tmp_path
    if web.state.session:
        web.state.session.close()
        web.state.session = None


def fill_all(client, diagrams=True):
    for part in spec_module.all_parts():
        if not part["inputs"]:
            continue
        assert client.post(f"/api/part/{part['key']}/inputs", json=filled(part)).status_code == 200
    for key in spec_module.report_keys():
        assert client.post(f"/api/part/{key}/generate").json()["ok"]
        client.post(f"/api/part/{key}/review", json={"reviewed": True})
    if diagrams:
        picture = io.BytesIO()
        Image.new("RGB", (900, 500), (60, 90, 140)).save(picture, format="PNG")
        for part in spec_module.all_parts():
            if (part.get("attach") or {}).get("caption_id"):
                client.post(f"/api/part/{part['key']}/attachments", files=[("files", ("diagram.png", picture.getvalue()))])


def test_every_template_part_has_a_page(app_client):
    client, _, _ = app_client
    for part in spec_module.all_parts():
        response = client.get(f"/part/{part['key']}", follow_redirects=False)
        assert response.status_code == 200, part["key"]
        assert part["label"].split()[0] in response.text


def test_every_master_anchor_is_reachable_from_a_part():
    from app.docbuild.builder import Builder, BuildOptions

    keys = spec_module.report_keys()
    anchors = [k for k, _ in Builder({}, {}, BuildOptions()).anchors()]
    for anchor in anchors:
        assert any(k == anchor or k.startswith(anchor + ".") for k in keys), anchor


def test_typed_session_exports_final_report_without_network(app_client, monkeypatch):
    client, web, tmp_path = app_client

    def blocked(*_args, **_kwargs):
        raise AssertionError("network used")

    fill_all(client)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    issues = [i for i in __import__("app.parts.export", fromlist=["x"]).run_checks(web.state.session) if i["blocking"]]
    assert issues == []
    response = client.post("/api/build", json={"final": True}).json()
    assert response["ok"], response
    document = Document(web.state.last_export)
    text = "\n".join("".join(t.text or "" for t in p.iter(qn("w:t"))) for p in document.element.body.iter(qn("w:p")))
    assert "SAMPLE VALUE" in text and TEXT in text
    for banned in ("{{", "[Not yet", "[Missing", "GUIDELINES"):
        assert banned not in text


def test_session_file_is_portable_and_complete(app_client, tmp_path):
    client, web, _ = app_client
    client.post("/api/part/ch1.scope/inputs", json={"scope": "My scope", "limits": "My limits"})
    client.post("/api/part/ch1.scope/generate")
    copy = tmp_path / "moved.rrg"
    web.state.session.copy_to(copy)
    other = Session(copy)
    assert other.inputs("ch1.scope")["scope"] == "My scope"
    assert "My limits" in other.draft("ch1.scope")["html"]
    other.close()


def test_final_is_blocked_until_reviewed(app_client):
    client, _, _ = app_client
    fill_all(client)
    client.post("/api/part/ch1.scope/review", json={"reviewed": False})
    assert client.post("/api/build", json={"final": True}).json()["ok"] is False
    assert client.post("/api/build", json={"final": False}).json()["ok"] is True


def test_editing_a_draft_clears_review_and_sanitizes(app_client):
    client, web, _ = app_client
    client.post("/api/part/ch1.scope/inputs", json={"scope": "x"})
    client.post("/api/part/ch1.scope/generate")
    client.post("/api/part/ch1.scope/review", json={"reviewed": True})
    client.post("/api/part/ch1.scope/draft", json={"html": "<p onclick='x'>Safe<script>bad()</script></p>"})
    assert web.state.session.status()["ch1.scope"] == "draft"
    assert "script" not in web.state.session.draft("ch1.scope")["html"]
    assert "onclick" not in web.state.session.draft("ch1.scope")["html"]


def test_restore_previous_version(app_client):
    client, web, _ = app_client
    client.post("/api/part/ch1.scope/inputs", json={"scope": "First"})
    client.post("/api/part/ch1.scope/generate")
    client.post("/api/part/ch1.scope/inputs", json={"scope": "Second"})
    client.post("/api/part/ch1.scope/generate")
    assert client.post("/api/part/ch1.scope/restore").json()["ok"]
    assert "First" in web.state.session.draft("ch1.scope")["html"]


def test_hypothesis_off_removes_parts_and_question(app_client):
    client, web, _ = app_client
    fill_all(client)
    client.post("/api/part/setup.options/inputs", json={"stage": "Final report", "iso_edition": "2011", "hypothesis": "No"})
    for key in ONE_PER_QUESTION:
        client.post(f"/api/part/{key}/inputs", json=filled(spec_module.part(key), questions=5))
    for key in ("ch1.sop", "ch3.statistics", *ONE_PER_QUESTION):
        client.post(f"/api/part/{key}/generate")
        client.post(f"/api/part/{key}/review", json={"reviewed": True})
    assert "ch1.hypothesis" in g.disabled_parts(web.state.session)
    assert "difference" not in g.research_question_ids(web.state.session)
    assert client.post("/api/build", json={"final": True}).json()["ok"]
    text = "\n".join(p.text for p in Document(web.state.last_export).paragraphs)
    assert "Research Hypothesis" not in text
    assert "Is there a significant difference" not in text


def test_part_download_writes_docx(app_client):
    client, web, _ = app_client
    fill_all(client)
    for key in ("ch1.scope", "prelim.cover", "prelim.title", "prelim.approval"):
        assert client.post(f"/api/part/{key}/download").json()["ok"], key
        Document(web.state.last_export)


def test_rejects_non_session_file(tmp_path):
    from app.session import SessionError

    bad = tmp_path / "bad.rrg"
    bad.write_bytes(b"this is not a database at all, not even close")
    with pytest.raises(SessionError):
        Session(bad)


def test_slovin_and_interpretation():
    assert g.slovin(120, 0.05) == 93  # 120 / 1.3 = 92.3, rounded up
    assert g.split_title("A " * 3 + "WORD" * 1)
    assert all(len(line) <= 42 for line in g.split_title("LABTRACK: A WEB-BASED LABORATORY EQUIPMENT INVENTORY AND BORROWING SYSTEM FOR THE SCIENCE DEPARTMENT"))
    _ = io
