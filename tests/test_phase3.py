import io
import os
import sys
from pathlib import Path

os.environ["RRG_NO_TOKEN"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
import pandas as pd  # noqa: E402
import pypdfium2  # noqa: E402
import pytest  # noqa: E402
from docx import Document  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.session import Session  # noqa: E402
from app.sources import apa  # noqa: E402
from app.stats import evaluation  # noqa: E402
from app.uploads.processing import UploadError, process_upload  # noqa: E402
from tests.test_app_flow import fill_all  # noqa: E402


def png(width=800, height=600, color=(200, 30, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format="PNG")
    return out.getvalue()


def pdf(pages=2) -> bytes:
    document = pypdfium2.PdfDocument.new()
    for _ in range(pages):
        document.new_page(300, 400)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("RRG_DATA_DIR", str(tmp_path / "data"))
    from app import web

    web.state.session = Session.create(tmp_path / "group.rrg")
    with TestClient(web.app) as client:
        yield client, web
    web.state.session.close()
    web.state.session = None


def upload(client, key, name, data):
    return client.post(f"/api/part/{key}/attachments", files=[("files", (name, data))]).json()


def inline_images(path) -> int:
    return len(Document(path).inline_shapes)


def test_image_is_downscaled():
    result = process_upload(png(5000, 3000), "big.png")[0]
    with Image.open(io.BytesIO(result.data)) as image:
        assert max(image.size) == 2200


def test_pdf_becomes_one_image_per_page():
    assert len(process_upload(pdf(3), "letter.pdf")) == 3


@pytest.mark.parametrize("raw", [b"", b"not an image at all"])
def test_bad_files_are_rejected(raw):
    with pytest.raises(UploadError):
        process_upload(raw, "x.png")


def test_attachments_are_stored_in_session_and_reach_word(app_client, tmp_path):
    client, web = app_client
    fill_all(client, diagrams=False)
    assert upload(client, "ch1.framework", "ipo.png", png())["added"] == 1
    assert upload(client, "app.permission", "letter.pdf", pdf(2))["added"] == 2
    assert client.post("/api/build", json={"final": False}).json()["ok"]
    assert inline_images(web.state.last_export) == 3
    copy = tmp_path / "copy.rrg"
    web.state.session.copy_to(copy)
    other = Session(copy)
    assert len(other.attachments("app.permission")) == 2
    assert other.attachment_data(other.attachments("ch1.framework")[0]["id"])[0]
    other.close()


def test_figure_is_numbered_and_mentioned(app_client):
    client, web = app_client
    fill_all(client, diagrams=False)
    upload(client, "ch1.framework", "ipo.png", png())
    client.post("/api/build", json={"final": False})
    text = "\n".join(p.text for p in Document(web.state.last_export).paragraphs)
    assert "Figure 1. Conceptual Framework of the Study" in text
    assert "Figure 1 presents the conceptual framework" in text


def test_documentation_photo_needs_consent(app_client):
    client, web = app_client
    fill_all(client, diagrams=False)
    upload(client, "app.documentation", "people.png", png())
    client.post("/api/build", json={"final": False})
    assert inline_images(web.state.last_export) == 0
    attachment = web.state.session.attachments("app.documentation")[0]
    client.post(f"/api/attachment/{attachment['id']}", json={"caption": "During evaluation", "consent": True})
    client.post("/api/build", json={"final": False})
    assert inline_images(web.state.last_export) == 1


def test_captioned_files_require_caption(app_client):
    client, web = app_client
    fill_all(client)
    upload(client, "ch4.design.modules", "login.png", png())
    from app.parts.export import run_checks

    assert any("caption" in i["message"] for i in run_checks(web.state.session))


def test_uploaded_document_counts_without_text(app_client):
    from app.parts.export import run_checks

    client, web = app_client
    upload(client, "app.validation", "cert.png", png())
    assert not any(i["key"] == "app.validation" for i in run_checks(web.state.session))


RESPONSES = pd.DataFrame({
    "Timestamp": ["t1", "t2", "t3", "t4"],
    "Full Name": ["Secret Person A", "Secret Person B", "Secret Person C", "Secret Person D"],
    "Email Address": ["a@x.org", "b@x.org", "c@x.org", "d@x.org"],
    "Respondent Type": ["IT Expert", "IT Expert", "End-User", "End-User"],
    "Usability [Easy to learn]": [5, 4, "Agree", "Strongly Agree"],
    "Usability [Attractive]": [5, 5, 3, 4],
    "Security [Passwords]": [4, 4, 5, 3],
    "Compatibility [Browsers]": [4, 3, None, None],
})


def csv_bytes() -> bytes:
    return RESPONSES.to_csv(index=False).encode("utf-8")


def test_evaluation_import_computes_and_stores_no_identity(app_client, tmp_path):
    client, web = app_client
    info = client.post("/api/import/evaluation", files={"file": ("r.csv", csv_bytes())}).json()
    assert info["guess"]["Respondent Type"] == "__group__"
    assert info["guess"]["Full Name"] == "" and info["guess"]["Email Address"] == ""
    assert info["guess"]["Usability [Easy to learn]"] == "Usability"
    assert client.post("/api/import/evaluation/apply", json={"mapping": info["guess"]}).json()["ok"]
    scores = {r["characteristic"]: r for r in web.state.session.inputs("ch4.acceptability")["scores"]}
    assert scores["Usability"]["experts"] == "4.75"  # (5+4+5+5)/4
    assert scores["Usability"]["users"] == "4.00"    # (4+5+3+4)/4
    assert scores["Compatibility"]["experts"] == "3.50" and scores["Compatibility"]["users"] == ""
    web.state.session.close()
    raw = (tmp_path / "group.rrg").read_bytes()
    web.state.session = Session(tmp_path / "group.rrg")
    assert b"Secret Person" not in raw and b"a@x.org" not in raw


def test_difference_matches_independent_calculation():
    from scipy import stats

    frame = pd.DataFrame({"Group": ["IT Expert"] * 3 + ["End-User"] * 3, "Usability q": [5, 4, 4, 3, 4, 2]})
    cleaned = evaluation.clean(frame, {"Group": "__group__", "Usability q": "Usability"})
    result = evaluation.compare_groups(cleaned)
    expected = stats.ttest_ind([5, 4, 4], [3, 4, 2], equal_var=False)
    assert result["t"] == f"{expected.statistic:.2f}" and result["p"] == f"{expected.pvalue:.3f}"
    assert "mann_whitney_p" in result


def test_cronbach_alpha_known_value():
    items = [[1, 2, 3, 4, 5], [2, 2, 3, 5, 5], [1, 3, 3, 4, 4]]
    # By hand: item variances 2.5 + 2.3 + 1.5 = 6.3; total variance 17.3; 1.5 * (1 - 6.3/17.3)
    assert round(evaluation.cronbach_alpha(items), 3) == 0.954


def test_import_rejects_missing_group():
    frame = pd.DataFrame({"Usability": [5]})
    with pytest.raises(evaluation.ImportError_):
        evaluation.clean(frame, {"Usability": "Usability"})


def test_apa_journal():
    source = {"authors": "Dela Cruz, J. P.; Santos, M.", "year": "2024", "title": "Inventory systems in schools", "container": "Journal of School Technology", "volume": "8", "issue": "2", "pages": "11–20", "link": "https://doi.org/10.1/x", "type": "Journal article"}
    assert apa.format_reference(source) == "Dela Cruz, J. P., &amp; Santos, M. (2024). Inventory systems in schools. <i>Journal of School Technology</i>, <i>8</i>(2), 11–20. https://doi.org/10.1/x"


def test_apa_book_and_thesis():
    book = apa.format_reference({"authors": "Reyes, A.", "year": "2020", "title": "Web systems", "container": "Rex Press", "type": "Book"})
    assert book == "Reyes, A. (2020). <i>Web systems.</i> Rex Press."
    thesis = apa.format_reference({"authors": "Cruz, B.", "year": "2021", "title": "A study", "container": "Cabiao SHS", "type": "Thesis"})
    assert "[Unpublished thesis]" in thesis


def test_citation_detection():
    text = "This agrees with (Dela Cruz, 2024) and Santos (2023), as in Table (2020). See (Reyes & Cruz, 2019)."
    found = apa.find_citations(text)
    assert ("Dela Cruz", "2024") in found and ("Santos", "2023") in found and any(y == "2019" for _, y in found)
    assert not any(n == "Table" for n, _ in found)


def test_references_only_include_cited_sources(app_client):
    client, web = app_client
    fill_all(client)
    client.post("/api/part/ch2.sources/inputs", json={"sources": [
        {"authors": "Cited, A.", "year": "2022", "title": "Used", "container": "J", "type": "Journal article"},
        {"authors": "Unused, B.", "year": "2021", "title": "Not used", "container": "J", "type": "Journal article"},
    ]})
    client.post("/api/part/ch1.scope/inputs", json={"scope": "Prior work agrees (Cited, 2022) on this.", "limits": ""})
    client.post("/api/part/ch1.scope/generate")
    client.post("/api/part/refs.list/inputs", json={"entries": ""})
    client.post("/api/part/refs.list/generate")
    from app.parts.export import run_checks

    blocks = web.state.session.draft("refs.list")["after"]
    assert len(blocks) == 1 and "Cited, A." in blocks[0]["html"]
    assert any("Not cited" in i["message"] and "Unused" in i["message"] for i in run_checks(web.state.session))


def test_uncited_source_flagged_for_unknown_citation(app_client):
    from app.parts.export import run_checks

    client, web = app_client
    client.post("/api/part/ch1.scope/inputs", json={"scope": "A claim (Ghost, 2020).", "limits": ""})
    client.post("/api/part/ch1.scope/generate")
    assert any("Ghost" in i["message"] for i in run_checks(web.state.session))


def test_lookup_parses_crossref_and_reports_offline():
    item = {"message": {"author": [{"family": "Cruz", "given": "Juan Pablo"}], "issued": {"date-parts": [[2021]]}, "title": ["A  title"], "container-title": ["Journal"], "volume": "3", "issue": "1", "page": "1-2", "DOI": "10.1/abc", "type": "journal-article"}}
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=item)))
    source = apa.lookup("10.1/abc 10.1000/abc", client)[0]
    assert source["authors"] == "Cruz, J. P." and source["title"] == "A title" and source["link"] == "https://doi.org/10.1/abc"

    def down(_request):
        raise httpx.ConnectError("offline")

    with pytest.raises(apa.LookupError_):
        apa.lookup("anything", httpx.Client(transport=httpx.MockTransport(down)))


def test_empty_lists_are_removed_when_no_figures(app_client):
    client, web = app_client
    fill_all(client, diagrams=False)
    client.post("/api/build", json={"final": False})
    text = "\n".join(p.text for p in Document(web.state.last_export).paragraphs)
    assert "LIST OF FIGURES" not in text and "LIST OF TABLES" in text
