import io
import sys
from pathlib import Path

import pytest
from docx import Document
from docx.oxml.ns import qn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.docbuild import BuildError, BuildOptions, build_report, build_single_part  # noqa: E402
from app.docbuild.standard import verbal_interpretation  # noqa: E402
from tests.fixtures.complete_report import facts, options_questions, parts  # noqa: E402


def body_text(data: bytes) -> str:
    document = Document(io.BytesIO(data))
    return "\n".join("".join(t.text or "" for t in p.iter(qn("w:t"))) for p in document.element.body.iter(qn("w:p")))


def build(**kwargs):
    return build_report(facts(), parts(), BuildOptions(research_questions=options_questions(), **kwargs))


def test_full_build_has_no_issues():
    result = build(final=True)
    assert result.issues == []
    assert result.tables["features"] == 1 and result.tables["difference"] == 10
    assert result.figures["ipo"] == 1 and result.figures["login"] == 6
    assert result.appendices["cv"] == "M"


def test_no_guide_content_or_placeholders():
    text = body_text(build(final=True).data)
    for banned in ("GUIDELINES", "CURRICULUM LINK", "GUIDE FOR LEARNERS", "[Write", "[Insert", "{{", "[Missing", "(Optional)"):
        assert banned not in text


def test_three_sections_preserved():
    document = Document(io.BytesIO(build(final=True).data))
    sections = document.element.body.findall(".//" + qn("w:sectPr"))
    assert len(sections) == 3
    formats = [s.find(qn("w:pgNumType")) for s in sections]
    assert formats[0] is None
    assert formats[1].get(qn("w:fmt")) == "lowerRoman" and formats[2].get(qn("w:fmt")) == "decimal"


def test_logos_kept():
    import zipfile

    archive = zipfile.ZipFile(io.BytesIO(build(final=True).data))
    source = zipfile.ZipFile(Path(__file__).resolve().parent.parent / "template" / "source-template.docx")
    logos = {info.file_size for info in source.infolist() if info.filename.startswith("word/media/")}
    built = {info.file_size for info in archive.infolist() if info.filename.startswith("word/media/")}
    assert logos <= built
    assert b"wpg:wgp" in archive.read("word/document.xml")


def test_disabling_optional_parts_renumbers():
    p = parts()
    del p["ch1.hypothesis"], p["ch4.difference"]
    p["ch3.statistics"] = [b for b in p["ch3.statistics"] if "t-test" not in b.get("html", "")]
    p["ch4.problems"][0] = {"type": "paragraph", "html": "See {{ref:rq:enhancements}}."}
    questions = [q for q in options_questions() if q != "difference"]
    disabled = {"ch1.hypothesis", "ch4.difference", "app.editing"}
    p.pop("app.editing")
    result = build_report(facts(), p, BuildOptions(final=True, disabled_parts=disabled, research_questions=questions))
    assert result.issues == []
    text = body_text(result.data)
    assert "Research Hypothesis" not in text and "Test of Significant Difference" not in text
    assert "See 5." in text
    assert result.appendices["revision"] == "K" and result.appendices["cv"] == "L"
    assert "Certificate of Editing" not in text
    assert "difference" not in result.tables


def test_reference_to_removed_appendix_is_reported():
    p = parts()
    p.pop("app.interview")
    result = build_report(facts(), p, BuildOptions(disabled_parts={"app.interview"}, research_questions=options_questions()))
    assert any("removed appendix: interview" in issue for issue in result.issues)


def test_missing_part_fails_final_but_not_draft():
    p = parts()
    p.pop("ch1.scope")
    questions = options_questions()
    with pytest.raises(BuildError):
        build_report(facts(), p, BuildOptions(final=True, research_questions=questions))
    draft = build_report(facts(), p, BuildOptions(research_questions=questions))
    assert "[Not yet written]" in body_text(draft.data)


def test_single_part_contains_only_that_part():
    data = build_single_part(facts(), parts(), "ch1.significance", BuildOptions(final=True, research_questions=options_questions()))
    text = body_text(data)
    assert "Significance of the Study" in text and "End-Users." in text
    assert "Background of the Study" not in text and "APPROVAL SHEET" not in text


def test_lists_restart():
    document = Document(io.BytesIO(build(final=True).data))
    ids = [n.get(qn("w:val")) for n in document.element.body.iter(qn("w:numId"))]
    assert len(set(ids)) >= 8


@pytest.mark.parametrize("value,expected", [(4.21, "Highly Acceptable"), (4.20, "Acceptable"), (4.204, "Acceptable"), (1.0, "Not Acceptable"), (5.0, "Highly Acceptable"), (2.605, "Moderately Acceptable")])
def test_verbal_interpretation(value, expected):
    assert verbal_interpretation(value) == expected
