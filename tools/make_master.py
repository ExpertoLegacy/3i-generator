"""Create template/master.docx from template/source-template.docx.

The source template is never modified. This script:
  * removes the guide boxes and the two guide sections,
  * replaces each writable region with an anchor paragraph ``{{part:key}}``,
  * replaces cover/title/approval placeholders with ``{{field:name}}`` tokens,
  * adds named styles and real TOC-style fields for the lists.

Element numbers refer to the direct children of ``w:body`` in the source. Every
region is checked against the expected text so a changed template fails loudly.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "template" / "source-template.docx"
MASTER = ROOT / "template" / "master.docx"

FONT = "Bookman Old Style"

GUIDE_PREFIXES = (
    "GUIDELINES",
    "CURRICULUM LINK",
    "HOW TO USE",
    "FORMAT",
    "WRITING",
    "BEFORE THE ORAL",
    "DURING AND AFTER",
)

# (first element, last element inclusive, anchor key, expected start of first text)
REGIONS = [
    (89, 89, "prelim.acknowledgment", "[Write your acknowledgment"),
    (93, 94, "prelim.abstract", "[Write your abstract"),
    (120, 120, "ch1.background", "[Write the background"),
    (124, 143, "ch1.sop", "This study aims"),
    (147, 154, "ch1.objectives", "The general objective"),
    (158, 158, "ch1.hypothesis", "Ho:"),
    (162, 165, "ch1.framework", "[Explain the conceptual"),
    (169, 169, "ch1.scope", "[Write the scope"),
    (173, 176, "ch1.significance", "End-Users."),
    (180, 181, "ch1.terms", "Usability."),
    (190, 193, "ch2.literature", "[Topic 1]"),
    (197, 200, "ch2.studies", "Foreign Studies"),
    (204, 204, "ch2.systems", "[Describe the related"),
    (208, 211, "ch2.synthesis", "[Write the synthesis"),
    (218, 218, "ch3.design", "[Write the research design"),
    (222, 245, "ch3.method", "[Introduce and justify"),
    (249, 254, "ch3.requirements", "Table 2."),
    (258, 258, "ch3.locale", "[Write the locale"),
    (262, 265, "ch3.population", "[Describe the population"),
    (269, 269, "ch3.sampling", "[Write the sampling"),
    (273, 276, "ch3.instrument", "[Describe the research"),
    (280, 288, "ch3.gathering", "Secured a letter"),
    (292, 302, "ch3.statistics", "Frequency and Percentage."),
    (306, 306, "ch3.ethics", "[Write the ethical"),
    (315, 318, "ch4.problems", "[Discuss the problems"),
    (322, 341, "ch4.design", "System Architecture"),
    (345, 348, "ch4.testing", "[Discuss the results"),
    (352, 355, "ch4.acceptability", "[Discuss the level"),
    (359, 362, "ch4.difference", "Table 10."),
    (366, 366, "ch4.enhancements", "[Write the proposed"),
    (373, 378, "ch5.summary", "[Write a short"),
    (382, 386, "ch5.conclusions", "[Conclusion 1]"),
    (390, 393, "ch5.recommendations", "[Recommendation 1]"),
    (397, 400, "refs.list", "Author, A. A., & Author, B. B."),
    (407, 407, "app.permission", "[Insert letter"),
    (411, 411, "app.consent", "[Insert informed"),
    (415, 418, "app.instrument", "Directions:"),
    (422, 422, "app.validation", "[Insert certificate of validation"),
    (426, 426, "app.interview", "[Insert interview"),
    (430, 430, "app.test_cases", "[Insert test cases"),
    (434, 436, "app.gantt", "Activity"),
    (440, 440, "app.manual", "[Insert user manual"),
    (444, 444, "app.code", "[Insert selected"),
    (448, 448, "app.documentation", "[Insert documentation"),
    (452, 452, "app.editing", "[Insert certificate of editing"),
    (456, 461, "app.revision", "No."),
    (465, 465, "app.cv", "[Insert curriculum"),
]

GUIDE_SECTIONS = [(51, 68, "GUIDE FOR LEARNERS"), (466, 474, "GUIDE: ORAL DEFENSE")]

# Empty spacer paragraphs that are part of the signed approval layout.
KEEP_EMPTY_AFTER_TITLE = {79, 81}
LAST_TITLE_PAGE_ELEMENT = 50

STYLES = f"""
<w:style {nsdecls('w')} w:type="paragraph" w:customStyle="1" w:styleId="ReportBody">
  <w:name w:val="Report Body"/><w:basedOn w:val="Normal"/><w:qFormat/>
  <w:pPr><w:spacing w:before="0" w:after="120" w:line="360" w:lineRule="auto"/>
    <w:ind w:firstLine="720"/><w:jc w:val="both"/></w:pPr>
  <w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/></w:rPr>
</w:style>|
<w:style {nsdecls('w')} w:type="paragraph" w:customStyle="1" w:styleId="ReportBodyNoIndent">
  <w:name w:val="Report Body No Indent"/><w:basedOn w:val="ReportBody"/><w:qFormat/>
  <w:pPr><w:ind w:firstLine="0"/></w:pPr>
</w:style>|
<w:style {nsdecls('w')} w:type="paragraph" w:customStyle="1" w:styleId="ReportListL0">
  <w:name w:val="Report List Level 0"/><w:basedOn w:val="Normal"/><w:qFormat/>
  <w:pPr><w:spacing w:before="0" w:after="80" w:line="360" w:lineRule="auto"/><w:jc w:val="both"/></w:pPr>
  <w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/></w:rPr>
</w:style>|
<w:style {nsdecls('w')} w:type="paragraph" w:customStyle="1" w:styleId="ReportListL1">
  <w:name w:val="Report List Level 1"/><w:basedOn w:val="ReportListL0"/><w:qFormat/>
  <w:pPr><w:spacing w:after="40"/></w:pPr>
</w:style>|
<w:style {nsdecls('w')} w:type="paragraph" w:customStyle="1" w:styleId="ReportCaptionTable">
  <w:name w:val="Report Caption Table"/><w:basedOn w:val="Normal"/><w:qFormat/>
  <w:pPr><w:keepNext/><w:spacing w:before="200" w:after="120" w:line="276" w:lineRule="auto"/><w:jc w:val="center"/></w:pPr>
  <w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/><w:b/><w:bCs/></w:rPr>
</w:style>|
<w:style {nsdecls('w')} w:type="paragraph" w:customStyle="1" w:styleId="ReportCaptionFigure">
  <w:name w:val="Report Caption Figure"/><w:basedOn w:val="Normal"/><w:qFormat/>
  <w:pPr><w:spacing w:before="120" w:after="240" w:line="276" w:lineRule="auto"/><w:jc w:val="center"/></w:pPr>
  <w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/><w:b/><w:bCs/></w:rPr>
</w:style>|
<w:style {nsdecls('w')} w:type="paragraph" w:customStyle="1" w:styleId="ReportFigure">
  <w:name w:val="Report Figure"/><w:basedOn w:val="Normal"/>
  <w:pPr><w:keepNext/><w:spacing w:before="120" w:after="0" w:line="240" w:lineRule="auto"/><w:jc w:val="center"/></w:pPr>
</w:style>|
<w:style {nsdecls('w')} w:type="paragraph" w:customStyle="1" w:styleId="ReportFormula">
  <w:name w:val="Report Formula"/><w:basedOn w:val="Normal"/>
  <w:pPr><w:spacing w:before="60" w:after="60" w:line="276" w:lineRule="auto"/><w:jc w:val="center"/></w:pPr>
  <w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/><w:b/><w:bCs/><w:i/><w:iCs/></w:rPr>
</w:style>|
<w:style {nsdecls('w')} w:type="paragraph" w:customStyle="1" w:styleId="ReportNote">
  <w:name w:val="Report Note"/><w:basedOn w:val="Normal"/>
  <w:pPr><w:spacing w:before="0" w:after="120" w:line="276" w:lineRule="auto"/><w:jc w:val="left"/></w:pPr>
  <w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/></w:rPr>
</w:style>|
<w:style {nsdecls('w')} w:type="paragraph" w:customStyle="1" w:styleId="ReportReference">
  <w:name w:val="Report Reference"/><w:basedOn w:val="Normal"/><w:qFormat/>
  <w:pPr><w:spacing w:before="0" w:after="120" w:line="360" w:lineRule="auto"/><w:ind w:left="720" w:hanging="720"/><w:jc w:val="left"/></w:pPr>
  <w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/></w:rPr>
</w:style>|
<w:style {nsdecls('w')} w:type="paragraph" w:customStyle="1" w:styleId="ReportTableCell">
  <w:name w:val="Report Table Cell"/><w:basedOn w:val="Normal"/>
  <w:pPr><w:spacing w:before="40" w:after="40" w:line="240" w:lineRule="auto"/><w:jc w:val="left"/></w:pPr>
  <w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/><w:sz w:val="20"/><w:szCs w:val="20"/></w:rPr>
</w:style>|
<w:style {nsdecls('w')} w:type="paragraph" w:customStyle="1" w:styleId="ReportFrontHeading">
  <w:name w:val="Report Front Heading"/><w:basedOn w:val="Normal"/>
  <w:pPr><w:keepNext/><w:keepLines/><w:spacing w:before="480" w:after="200"/><w:jc w:val="center"/></w:pPr>
  <w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/><w:b/><w:bCs/><w:color w:val="000000"/><w:sz w:val="28"/></w:rPr>
</w:style>|
<w:style {nsdecls('w')} w:type="paragraph" w:styleId="TOC1">
  <w:name w:val="toc 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:uiPriority w:val="39"/><w:unhideWhenUsed/>
  <w:pPr><w:tabs><w:tab w:val="right" w:leader="dot" w:pos="8296"/></w:tabs><w:spacing w:before="120" w:after="60" w:line="276" w:lineRule="auto"/><w:ind w:right="567"/><w:jc w:val="left"/></w:pPr>
  <w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/><w:b/><w:bCs/></w:rPr>
</w:style>|
<w:style {nsdecls('w')} w:type="paragraph" w:styleId="TOC2">
  <w:name w:val="toc 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:uiPriority w:val="39"/><w:unhideWhenUsed/>
  <w:pPr><w:tabs><w:tab w:val="right" w:leader="dot" w:pos="8296"/></w:tabs><w:spacing w:after="60" w:line="276" w:lineRule="auto"/><w:ind w:left="284" w:right="567"/><w:jc w:val="left"/></w:pPr>
  <w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/></w:rPr>
</w:style>|
<w:style {nsdecls('w')} w:type="paragraph" w:styleId="TOC3">
  <w:name w:val="toc 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:uiPriority w:val="39"/><w:unhideWhenUsed/>
  <w:pPr><w:tabs><w:tab w:val="right" w:leader="dot" w:pos="8296"/></w:tabs><w:spacing w:after="60" w:line="276" w:lineRule="auto"/><w:ind w:left="567" w:right="567"/><w:jc w:val="left"/></w:pPr>
  <w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/></w:rPr>
</w:style>|
<w:style {nsdecls('w')} w:type="paragraph" w:styleId="TableofFigures">
  <w:name w:val="table of figures"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:uiPriority w:val="99"/><w:unhideWhenUsed/>
  <w:pPr><w:tabs><w:tab w:val="right" w:leader="dot" w:pos="8296"/></w:tabs><w:spacing w:after="120" w:line="276" w:lineRule="auto"/><w:ind w:left="1134" w:right="567" w:hanging="1134"/><w:jc w:val="left"/></w:pPr>
  <w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/></w:rPr>
</w:style>
"""

HEADING3_PPR = '<w:spacing {ns} w:before="200" w:after="100"/>'


def text_of(element) -> str:
    return "".join(t.text or "" for t in element.iter(qn("w:t"))).strip()


def is_paragraph(element) -> bool:
    return element.tag == qn("w:p")


def is_table(element) -> bool:
    return element.tag == qn("w:tbl")


def has_sect(element) -> bool:
    return is_paragraph(element) and element.find(qn("w:pPr") + "/" + qn("w:sectPr")) is not None


def is_guide_box(element) -> bool:
    if not is_table(element):
        return False
    rows = element.findall(qn("w:tr"))
    if len(rows) != 1 or len(rows[0].findall(qn("w:tc"))) != 1:
        return False
    return text_of(element).startswith(GUIDE_PREFIXES)


def rewrite(paragraph, segments) -> None:
    """Replace all runs in a paragraph, keeping its pPr. Segments: (text, bold)."""
    size = None
    for run in paragraph.findall(qn("w:r")):
        rpr = run.find(qn("w:rPr"))
        if rpr is not None and rpr.find(qn("w:sz")) is not None:
            size = rpr.find(qn("w:sz")).get(qn("w:val"))
        paragraph.remove(run)
    for text, bold in segments:
        bold_xml = "<w:b/><w:bCs/>" if bold else ""
        size_xml = f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>' if size else ""
        paragraph.append(
            parse_xml(
                f'<w:r {nsdecls("w")}><w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/>'
                f'{bold_xml}{size_xml}</w:rPr><w:t xml:space="preserve">{text}</w:t></w:r>'
            )
        )


def anchor(key: str):
    return parse_xml(
        f'<w:p {nsdecls("w")}><w:pPr><w:pStyle w:val="ReportBody"/></w:pPr>'
        f"<w:r><w:t>{{{{part:{key}}}}}</w:t></w:r></w:p>"
    )


def toc_field(instruction: str, note: str):
    return parse_xml(
        f'<w:p {nsdecls("w")}><w:pPr><w:spacing w:before="120" w:after="120"/></w:pPr>'
        '<w:r><w:fldChar w:fldCharType="begin" w:dirty="true"/></w:r>'
        f'<w:r><w:instrText xml:space="preserve"> {instruction} </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        f'<w:r><w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/></w:rPr><w:t>{note}</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
    )


def apply_fields(els) -> None:
    b = True
    rewrite(els[10], [("{{field:subject_upper}}", b)])
    rewrite(els[12], [("Research Theme: {{field:research_theme}}", False)])
    rewrite(els[13], [("{{field:strand_section}}", False)])
    rewrite(els[18], [("{{repeat:proponents_upper}}", b)])
    rewrite(els[22], [("{{repeat:title_lines}}", b)])
    rewrite(els[29], [("{{field:school_name}}", False)])
    rewrite(els[30], [("{{field:school_address}}", False)])
    rewrite(els[34], [("{{field:subject}}", False)])
    rewrite(els[39], [("{{repeat:proponents_upper}}", b)])
    rewrite(els[46], [("{{field:adviser_upper}}", b)])
    rewrite(els[50], [("{{field:month_year}}", b)])
    rewrite(
        els[72],
        [
            ("This research project entitled “", False),
            ("{{field:title}}", b),
            ("”, prepared and submitted by {{field:proponents_joined}}, in partial fulfillment of the "
             "requirements in {{field:subject}}, has been examined and is hereby recommended for oral "
             "examination.", False),
        ],
    )
    rewrite(els[73], [("{{field:adviser_upper}}", b)])
    rewrite(els[77], [("{{field:panel_chair_upper}}", b)])
    cells = els[80].findall(".//" + qn("w:tc"))
    for index, cell in enumerate(cells, start=1):
        rewrite(cell.findall(qn("w:p"))[0], [(f"{{{{field:panel_member_{index}_upper}}}}", b)])
    rewrite(
        els[82],
        [("Accepted and approved in partial fulfillment of the requirements in {{field:subject}}.", False)],
    )
    rewrite(els[83], [("{{field:principal_upper}}", b)])
    rewrite(els[84], [("{{field:principal_title}}", False)])


def add_styles(document) -> None:
    styles = document.styles.element
    for chunk in STYLES.split("|"):
        chunk = chunk.strip()
        if chunk:
            styles.append(parse_xml(chunk))
    # The template's Heading 3 style is blue 14 pt and overridden by hand. Fix the style.
    for style in styles.findall(qn("w:style")):
        if style.get(qn("w:styleId")) == "Heading3":
            ppr = style.find(qn("w:pPr"))
            spacing = ppr.find(qn("w:spacing"))
            spacing.set(qn("w:before"), "200")
            spacing.set(qn("w:after"), "100")
            old = style.find(qn("w:rPr"))
            style.replace(
                old,
                parse_xml(
                    f'<w:rPr {nsdecls("w")}><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}" '
                    'w:eastAsiaTheme="majorEastAsia"/><w:b/><w:bCs/><w:i/><w:iCs/>'
                    '<w:color w:val="000000"/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr>'
                ),
            )


def add_bullet_numbering(document) -> None:
    """Add abstract numbering 900 (bullets) used by the builder."""
    numbering = document.part.numbering_part.element
    abstract = parse_xml(
        f'<w:abstractNum {nsdecls("w")} w:abstractNumId="900"><w:multiLevelType w:val="hybridMultilevel"/>'
        '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="&#8226;"/>'
        '<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr>'
        f'<w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:hint="default"/></w:rPr></w:lvl></w:abstractNum>'
    )
    first_num = numbering.find(qn("w:num"))
    first_num.addprevious(abstract)


def main() -> None:
    document = Document(str(SOURCE))
    body = document.element.body
    els = list(body)
    if len(els) != 476:
        sys.exit(f"Template changed: expected 476 body elements, found {len(els)}")

    for start, _end, marker in GUIDE_SECTIONS:
        if not text_of(els[start]).startswith(marker):
            sys.exit(f"Template changed at element {start}: {text_of(els[start])[:50]!r}")

    add_styles(document)
    add_bullet_numbering(document)
    apply_fields(els)

    drop: set[int] = set()
    for start, end, _ in GUIDE_SECTIONS:
        drop.update(range(start, end + 1))
    drop.update({19, 20, 23, 40, 41})
    drop.update(i for i, el in enumerate(els) if is_guide_box(el))

    anchors: dict[int, str] = {}
    for start, end, key, expected in REGIONS:
        found = text_of(els[start])
        if not found.startswith(expected):
            sys.exit(f"Template changed at {key} ({start}): {found[:60]!r}")
        anchors[start] = key
        drop.update(range(start, end + 1))

    for index, element in enumerate(els):
        empty = is_paragraph(element) and not text_of(element) and not has_sect(element)
        no_drawing = element.find(".//" + qn("w:drawing")) is None
        if index > LAST_TITLE_PAGE_ELEMENT and empty and no_drawing and index not in KEEP_EMPTY_AFTER_TITLE:
            drop.add(index)

    # Remove "(Optional)" from headings that stay. Optional parts are switched in Report Options.
    for index in (155, 356):
        for text in els[index].iter(qn("w:t")):
            if text.text and "(Optional)" in text.text:
                text.text = text.text.replace(" (Optional)", "")

    # The contents heading must not list itself in the table of contents.
    els[95].find(qn("w:pPr")).find(qn("w:pStyle")).set(qn("w:val"), "ReportFrontHeading")

    # TOC placeholder wording.
    for text in els[98].iter(qn("w:t")):
        text.text = "Open this file in Word and choose Yes to update the table of contents."

    # Real lists of tables and figures, replacing the typed placeholders.
    page_label = (
        f'<w:p {nsdecls("w")}><w:pPr><w:spacing w:before="120" w:after="120"/><w:jc w:val="right"/></w:pPr>'
        f'<w:r><w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/><w:b/><w:bCs/></w:rPr>'
        "<w:t>Page</w:t></w:r></w:p>"
    )
    for first, label in ((102, "Table"), (109, "Figure")):
        els[first].addprevious(parse_xml(page_label))
        els[first].addprevious(
            toc_field(f'TOC \\h \\z \\c "{label}"', f"Open this file in Word and choose Yes to update this list.")
        )
        drop.update(range(first, first + 4))

    for index, key in anchors.items():
        els[index].addprevious(anchor(key))

    for index in sorted(drop, reverse=True):
        if els[index].tag != qn("w:sectPr"):
            body.remove(els[index])

    document.core_properties.title = ""
    document.save(str(MASTER))

    remaining = [text_of(e) for e in body if is_paragraph(e) or is_table(e)]
    joined = "\n".join(remaining)
    print(f"Master written: {MASTER}")
    print(f"Body elements: {len(list(body))}, anchors: {len(anchors)}")
    for banned in ("GUIDELINES", "CURRICULUM LINK", "GUIDE FOR LEARNERS", "[Insert", "[Write"):
        if banned in joined:
            sys.exit(f"Guide content left in master: {banned}")
    _ = copy  # keep import list explicit for future template adjustments


if __name__ == "__main__":
    main()
