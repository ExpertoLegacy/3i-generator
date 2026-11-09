"""Checks for conditions that differ from the development computer."""
import ssl

import httpx
import pytest

from tests.test_ai import world  # noqa: F401, I001 - first, because it puts the app in test mode

from app import computer_check, net  # noqa: E402
from app.diagrams import draw  # noqa: E402
from app.main import window_size  # noqa: E402
from app.session import Session  # noqa: E402


def test_connection_trusts_bundled_and_windows_certificates():
    context = net.trusted_context()
    assert isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert len(context.get_ca_certs()) > 100


def test_windows_proxy_setting_is_used(monkeypatch):
    seen = {}

    class Recorder:
        def __init__(self, **options):
            seen.update(options)

    monkeypatch.setattr(net.httpx, "HTTPTransport", Recorder)
    monkeypatch.setattr(net, "getproxies", lambda: {"https": "http://proxy.school.test:3128"})
    net.transport()
    assert seen["proxy"] == "http://proxy.school.test:3128" and seen["local_address"] == "0.0.0.0" and isinstance(seen["verify"], ssl.SSLContext)


def test_no_proxy_means_a_direct_connection(monkeypatch):
    monkeypatch.setattr(net, "getproxies", dict)
    assert net.transport() is not None


@pytest.mark.parametrize(("error", "words"), [
    (httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"), "date and time"),
    (httpx.ProxyError("407 Proxy Authentication Required"), "proxy"),
    (httpx.ConnectTimeout("timed out"), "timed out"),
    (httpx.ConnectError("getaddrinfo failed"), "internet connection"),
])
def test_network_failures_are_explained(error, words):
    assert words in net.explain(error)


@pytest.mark.parametrize(("screen", "expected"), [((1920, 1080), (1360, 860)), ((1366, 768), (1326, 688)), ((1024, 600), (984, 520))])
def test_window_fits_the_screen(screen, expected):
    assert window_size(*screen) == expected


def test_check_page_lists_every_item_and_reads_no_report(world):  # noqa: F811
    client, web, _relay = world
    web.state.session.save_inputs("ch1.scope", {"scope": "PRIVATE STUDENT TEXT"})
    page = client.get("/check-computer").text
    for name in ("Windows", "Microsoft Word", "Bookman Old Style", "Free disk space", "Drawing diagrams", "Saving reports", "AI connection"):
        assert name in page
    assert "PRIVATE STUDENT TEXT" not in page


def test_check_reports_missing_font_and_word_as_notes_not_failures(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDIR", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert computer_check.report_font()["status"] == computer_check.WARN
    monkeypatch.setattr(computer_check, "_registry_value", lambda *_a: "")
    assert computer_check.word()["status"] == computer_check.WARN


def test_old_windows_is_a_problem(monkeypatch):
    class Old:
        build = 14393

    monkeypatch.setattr(computer_check.sys, "getwindowsversion", lambda: Old)
    assert computer_check.windows()["status"] == computer_check.PROBLEM


def test_one_crashing_check_does_not_hide_the_rest(monkeypatch):
    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(computer_check, "CHECKS", [broken, computer_check.disk])
    results = computer_check.run()["results"]
    assert len(results) == 2 and results[0]["status"] == computer_check.WARN and results[1]["name"] == "Free disk space"


def test_blocked_network_is_a_note(monkeypatch):
    def refuse(_request):
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]")

    monkeypatch.setenv("RRG_RELAY_URL", "https://relay.test")
    result = computer_check.ai_connection(httpx.Client(transport=httpx.MockTransport(refuse)))
    assert result["status"] == computer_check.WARN and "date and time" in result["detail"]


ACCENTED = "Peñaranda José Niño's Files"


def test_accented_folders_work_for_reports_and_diagrams(tmp_path, monkeypatch):
    folder = tmp_path / ACCENTED
    folder.mkdir()
    monkeypatch.setenv("TEMP", str(folder))
    monkeypatch.setenv("TMP", str(folder))
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    spec = {"nodes": [{"id": "a", "label": "Señor Niño", "type": "Actor"}, {"id": "b", "label": "Gumawa ng ulat", "type": "Use case"}], "edges": [{"from": "a", "to": "b"}]}
    assert draw("usecase", spec, "Sistema ng Paaralan").png
    session = Session.create(folder / "Ulat ni Peñaranda.rrg")
    session.save_inputs("ch1.scope", {"scope": "Ñ"})
    session.close()
    reopened = Session(folder / "Ulat ni Peñaranda.rrg")
    assert reopened.inputs("ch1.scope")["scope"] == "Ñ"
    reopened.close()
