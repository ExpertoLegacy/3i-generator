"""Build the Word report from template/master.docx and typed content blocks.

Inputs
    facts: dict of shared values used by ``{{field:...}}`` tokens.
    parts: dict of part_key -> list of blocks. A master anchor ``ch3.method`` receives
           part ``ch3.method`` and every ``ch3.method.*`` part, in dict order.
    options: BuildOptions.

Block types: html, paragraph, heading3, list, table, figure, formula, note, reference,
page_break. See the handlers below for the fields of each.

Tokens allowed in any text:
    {{ref:table:id}} {{ref:figure:id}} {{ref:rq:id}} {{ref:appendix:key}}
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any, Iterable

import lxml.html
from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Emu, Inches
from PIL import Image

import sys

_BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))
MASTER = _BASE / "template" / "master.docx"

TEXT_WIDTH = 8306  # twips, the usable A4 width in sections 2 and 3
TEXT_WIDTH_IN = TEXT_WIDTH / 1440
MAX_IMAGE_HEIGHT_IN = 7.8
NUMBERED_ABSTRACT = "91"  # the template's own "1." / "a." list definition
BULLET_ABSTRACT = "900"

OPTIONAL_PARTS = {"ch1.hypothesis", "ch4.difference"}
TOKEN = re.compile(r"\{\{ref:(table|figure|rq|appendix):([\w.\-]+)\}\}")
ANCHOR = re.compile(r"^\{\{part:([\w.\-]+)\}\}$")
FIELD = re.compile(r"\{\{field:(\w+)\}\}")
REPEAT = re.compile(r"^\{\{repeat:(\w+)\}\}$")
W_NS = nsdecls("w")


class BuildError(Exception):
    pass


@dataclass
class BuildOptions:
    final: bool = False
    disabled_parts: set[str] = field(default_factory=set)
    research_questions: list[str] = field(default_factory=list)  # ids in order


@dataclass
class BuildResult:
    data: bytes
    issues: list[str]
    tables: dict[str, int]
    figures: dict[str, int]
    appendices: dict[str, str]


def _x(text: Any) -> str:
    return escape(str(text), quote=False)


class Builder:
    def __init__(self, facts: dict[str, Any], parts: dict[str, list[dict]], options: BuildOptions, master: Path = MASTER):
        self.facts = facts
        self.parts = parts
        self.options = options
        self.document = Document(str(master))
        self.body = self.document.element.body
        self.issues: list[str] = []
        self.tables: dict[str, int] = {}
        self.figures: dict[str, int] = {}
        self.appendices: dict[str, str] = {}
        self.rq = {rq_id: n for n, rq_id in enumerate(options.research_questions, start=1)}
        self._bookmark_id = 100
        numbering = self.document.part.numbering_part.element
        self.numbering = numbering
        self._next_num = max(int(n.get(qn("w:numId"))) for n in numbering.findall(qn("w:num"))) + 1
        self._mentioned: set[tuple[str, str]] = set()

    # ---- orchestration -------------------------------------------------------------

    def anchors(self) -> list[tuple[str, Any]]:
        found = []
        for element in self.body:
            if element.tag == qn("w:p"):
                match = ANCHOR.match(self._text(element))
                if match:
                    found.append((match.group(1), element))
        return found

    def blocks_for(self, key: str) -> list[dict] | None:
        selected: list[dict] = []
        hit = False
        for part_key, blocks in self.parts.items():
            if part_key == key or part_key.startswith(key + "."):
                hit = True
                selected.extend(blocks)
        return selected if hit else None

    def is_disabled(self, key: str) -> bool:
        return key in self.options.disabled_parts

    def assign_numbers(self, anchors) -> None:
        table_n = figure_n = 0
        letters = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        for key, _ in anchors:
            if self.is_disabled(key):
                continue
            if key.startswith("app."):
                self.appendices[key.split(".", 1)[1]] = next(letters)
            for block in self.blocks_for(key) or []:
                caption_id = block.get("caption_id")
                if not caption_id:
                    continue
                if block["type"] == "table":
                    table_n += 1
                    self._unique(self.tables, caption_id, table_n)
                elif block["type"] == "figure":
                    figure_n += 1
                    self._unique(self.figures, caption_id, figure_n)

    def _unique(self, registry: dict, key: str, value: int) -> None:
        if key in registry:
            raise BuildError(f"Duplicate caption id: {key}")
        registry[key] = value

    def build(self) -> BuildResult:
        anchors = self.anchors()
        self.assign_numbers(anchors)
        self._fill_front_matter()
        for key, element in anchors:
            if self.is_disabled(key):
                self._remove_with_heading(element)
                continue
            blocks = self.blocks_for(key)
            if key.startswith("app."):
                self._set_appendix_letter(element, self.appendices[key.split(".", 1)[1]])
            if blocks is None:
                message = f"Not yet written: {key}"
                self.issues.append(message)
                if self.options.final:
                    raise BuildError(message)
                blocks = [{"type": "paragraph", "text": "[Not yet written]", "indent": False}]
            for block in blocks:
                for new in self._render(block):
                    element.addprevious(new)
            self.body.remove(element)
        self._drop_empty_lists()
        if not self.options.final:
            self._mark_draft()
        self._check_leftovers()
        out = io.BytesIO()
        self.document.save(out)
        return BuildResult(out.getvalue(), self.issues, self.tables, self.figures, self.appendices)

    # ---- front matter -------------------------------------------------------------

    def _fill_front_matter(self) -> None:
        for paragraph in list(self.body.iter(qn("w:p"))):
            text = self._text(paragraph)
            repeat = REPEAT.match(text)
            if repeat:
                values = self.facts.get(repeat.group(1)) or []
                if not values:
                    self.issues.append(f"Missing value: {repeat.group(1)}")
                    values = ["[Missing]"]
                self._repeat_paragraph(paragraph, values)
                continue
            if "{{field:" in text:
                for t in paragraph.iter(qn("w:t")):
                    if t.text and "{{field:" in t.text:
                        t.text = FIELD.sub(self._field_value, t.text)

    def _field_value(self, match: re.Match) -> str:
        name = match.group(1)
        value = self.facts.get(name)
        if not value:
            self.issues.append(f"Missing value: {name}")
            return "[Missing]"
        return str(value)

    def _repeat_paragraph(self, paragraph, values: Iterable[str]) -> None:
        import copy

        for value in values:
            clone = copy.deepcopy(paragraph)
            for t in clone.iter(qn("w:t")):
                t.text = value
            paragraph.addprevious(clone)
        paragraph.getparent().remove(paragraph)

    def _remove_with_heading(self, anchor_element) -> None:
        previous = anchor_element.getprevious()
        if previous is not None and self._is_heading(previous):
            self.body.remove(previous)
        self.body.remove(anchor_element)

    def _is_heading(self, element) -> bool:
        if element.tag != qn("w:p"):
            return False
        style = element.find(qn("w:pPr") + "/" + qn("w:pStyle"))
        return style is not None and style.get(qn("w:val"), "").startswith("Heading")

    def _set_appendix_letter(self, anchor_element, letter: str) -> None:
        heading = anchor_element.getprevious()
        if heading is None or not self._is_heading(heading):
            raise BuildError("Appendix anchor has no heading")
        first = next(heading.iter(qn("w:t")))
        first.text = f"Appendix {letter}"

    def _text(self, element) -> str:
        return "".join(t.text or "" for t in element.iter(qn("w:t")))

    def _drop_empty_lists(self) -> None:
        """Remove a list page when the report has no tables or no figures of that kind."""
        for label, registry, heading in (("Table", self.tables, "LIST OF TABLES"), ("Figure", self.figures, "LIST OF FIGURES")):
            if registry:
                continue
            elements = list(self.body)
            for index, element in enumerate(elements):
                if element.tag == qn("w:p") and self._text(element) == heading:
                    for doomed in elements[index: index + 3]:  # heading, "Page" label, list field
                        self.body.remove(doomed)
                    break

    def _mark_draft(self) -> None:
        for section in self.document.sections:
            header = section.header
            if header.is_linked_to_previous:
                continue
            for paragraph in header.paragraphs:
                if any(r.tag == qn("w:r") for r in paragraph._p):
                    first = paragraph._p.find(qn("w:r"))
                    first.addprevious(
                        parse_xml(
                            f'<w:r {W_NS}><w:rPr><w:b/><w:color w:val="C00000"/></w:rPr>'
                            '<w:t xml:space="preserve">DRAFT     </w:t></w:r>'
                        )
                    )
                    break

    def _check_leftovers(self) -> None:
        for paragraph in self.body.iter(qn("w:p")):
            text = self._text(paragraph)
            for pattern in ("{{part:", "{{field:", "{{repeat:", "{{ref:"):
                if pattern in text:
                    self.issues.append(f"Unresolved token in: {text[:80]}")

    # ---- block rendering ----------------------------------------------------------

    def _render(self, block: dict) -> list:
        handler = getattr(self, "_b_" + block["type"], None)
        if handler is None:
            raise BuildError(f"Unknown block type: {block['type']}")
        return handler(block)

    def _b_paragraph(self, block: dict) -> list:
        style = "ReportBody" if block.get("indent", True) else "ReportBodyNoIndent"
        return [self._para(style, self._inline_source(block))]

    def _b_reference(self, block: dict) -> list:
        return [self._para("ReportReference", self._inline_source(block))]

    def _b_note(self, block: dict) -> list:
        return [self._para("ReportNote", self._inline_source(block))]

    def _b_formula(self, block: dict) -> list:
        return [self._para("ReportFormula", self._inline_source(block))]

    def _b_heading3(self, block: dict) -> list:
        return [self._para("Heading3", self._inline_source(block))]

    def _b_page_break(self, _block: dict) -> list:
        return [parse_xml(f'<w:p {W_NS}><w:r><w:br w:type="page"/></w:r></w:p>')]

    def _inline_source(self, block: dict) -> str:
        if "html" in block:
            return block["html"]
        return _x(block.get("text", ""))

    def _b_html(self, block: dict) -> list:
        """Sanitized editor HTML: p, ol, ul, li, strong, em."""
        wrapper = lxml.html.fragment_fromstring(block["html"] or "", create_parent="div")
        output: list = []
        for child in wrapper:
            tag = child.tag.lower()
            if tag in ("ol", "ul"):
                items = [{"html": lxml.html.tostring(li, encoding="unicode", method="html")} for li in child]
                output += self._b_list({"ordered": tag == "ol", "items": items})
            elif tag in ("h1", "h2", "h3", "h4"):
                html = lxml.html.tostring(child, encoding="unicode", method="html")
                output.append(self._para("Heading3", html))
            else:
                html = lxml.html.tostring(child, encoding="unicode", method="html")
                output.append(
                    self._para("ReportBody" if block.get("indent", True) else "ReportBodyNoIndent", html)
                )
        if wrapper.text and wrapper.text.strip():
            output.insert(0, self._para("ReportBody", _x(wrapper.text)))
        return output

    def _new_list(self, ordered: bool) -> str:
        num_id = str(self._next_num)
        self._next_num += 1
        abstract = NUMBERED_ABSTRACT if ordered else BULLET_ABSTRACT
        overrides = "".join(
            f'<w:lvlOverride w:ilvl="{i}"><w:startOverride w:val="1"/></w:lvlOverride>' for i in range(2)
        ) if ordered else ""
        self.numbering.append(
            parse_xml(f'<w:num {W_NS} w:numId="{num_id}"><w:abstractNumId w:val="{abstract}"/>{overrides}</w:num>')
        )
        return num_id

    def _b_list(self, block: dict) -> list:
        ordered = block.get("ordered", True)
        num_id = self._new_list(ordered)
        out = []
        for item in block["items"]:
            if isinstance(item, str):
                item = {"text": item}
            out.append(self._list_item(item, num_id, 0))
            for child in item.get("children", []):
                if isinstance(child, str):
                    child = {"text": child}
                out.append(self._list_item(child, num_id, 1))
        return out

    def _list_item(self, item: dict, num_id: str, level: int):
        style = f"ReportListL{level}"
        extra = f'<w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="{num_id}"/></w:numPr>'
        return self._para(style, self._inline_source(item), extra_ppr=extra)

    def _para(self, style: str, html_source: str, extra_ppr: str = ""):
        paragraph = parse_xml(f'<w:p {W_NS}><w:pPr><w:pStyle w:val="{style}"/>{extra_ppr}</w:pPr></w:p>')
        for run in self._inline_runs(html_source):
            paragraph.append(run)
        return paragraph

    def _inline_runs(self, source: str) -> list:
        root = lxml.html.fragment_fromstring(source or "", create_parent="div")
        runs: list = []

        def walk(node, bold: bool, italic: bool) -> None:
            if node.text:
                runs.extend(self._text_runs(node.text, bold, italic))
            for child in node:
                tag = child.tag.lower() if isinstance(child.tag, str) else ""
                if tag == "br":
                    runs.append(parse_xml(f"<w:r {W_NS}><w:br/></w:r>"))
                else:
                    walk(child, bold or tag in ("b", "strong"), italic or tag in ("i", "em"))
                if child.tail:
                    runs.extend(self._text_runs(child.tail, bold, italic))

        walk(root, False, False)
        return runs

    def _rpr(self, bold: bool, italic: bool) -> str:
        parts = []
        if bold:
            parts.append("<w:b/><w:bCs/>")
        if italic:
            parts.append("<w:i/><w:iCs/>")
        return f"<w:rPr>{''.join(parts)}</w:rPr>" if parts else ""

    def _text_runs(self, text: str, bold: bool, italic: bool) -> list:
        out = []
        position = 0
        for match in TOKEN.finditer(text):
            if match.start() > position:
                out.append(self._run(text[position:match.start()], bold, italic))
            out.extend(self._token(match.group(1), match.group(2), bold, italic))
            position = match.end()
        if position < len(text):
            out.append(self._run(text[position:], bold, italic))
        return out

    def _run(self, text: str, bold: bool, italic: bool):
        return parse_xml(
            f'<w:r {W_NS}>{self._rpr(bold, italic)}<w:t xml:space="preserve">{_x(text)}</w:t></w:r>'
        )

    def _token(self, kind: str, key: str, bold: bool, italic: bool) -> list:
        self._mentioned.add((kind, key))
        if kind == "rq":
            if key not in self.rq:
                self.issues.append(f"Unknown research question reference: {key}")
                return [self._run("[missing question]", bold, italic)]
            return [self._run(str(self.rq[key]), bold, italic)]
        if kind == "appendix":
            if key not in self.appendices:
                self.issues.append(f"Reference to removed appendix: {key}")
                return [self._run("[missing appendix]", bold, italic)]
            return [self._run(f"Appendix {self.appendices[key]}", bold, italic)]
        registry = self.tables if kind == "table" else self.figures
        if key not in registry:
            self.issues.append(f"Reference to missing {kind}: {key}")
            return [self._run(f"[missing {kind}]", bold, italic)]
        cached = f"{kind.capitalize()} {registry[key]}"
        return self._field(f" REF {self._bookmark_name(kind, key)} \\h ", cached, self._rpr(bold, italic))

    def _bookmark_name(self, kind: str, key: str) -> str:
        return "_Ref_" + kind[:3] + "_" + re.sub(r"\W", "_", key)[:30]

    def _field(self, instruction: str, cached: str, rpr: str = "") -> list:
        return [
            parse_xml(f'<w:r {W_NS}>{rpr}<w:fldChar w:fldCharType="begin"/></w:r>'),
            parse_xml(f'<w:r {W_NS}>{rpr}<w:instrText xml:space="preserve">{instruction}</w:instrText></w:r>'),
            parse_xml(f'<w:r {W_NS}>{rpr}<w:fldChar w:fldCharType="separate"/></w:r>'),
            parse_xml(f'<w:r {W_NS}>{rpr}<w:t>{_x(cached)}</w:t></w:r>'),
            parse_xml(f'<w:r {W_NS}>{rpr}<w:fldChar w:fldCharType="end"/></w:r>'),
        ]

    def _caption(self, kind: str, key: str, title: str):
        label = kind.capitalize()
        registry = self.tables if kind == "table" else self.figures
        style = "ReportCaptionTable" if kind == "table" else "ReportCaptionFigure"
        paragraph = parse_xml(f'<w:p {W_NS}><w:pPr><w:pStyle w:val="{style}"/></w:pPr></w:p>')
        self._bookmark_id += 1
        bid = self._bookmark_id
        paragraph.append(
            parse_xml(f'<w:bookmarkStart {W_NS} w:id="{bid}" w:name="{self._bookmark_name(kind, key)}"/>')
        )
        paragraph.append(self._run(f"{label} ", False, False))
        for run in self._field(f" SEQ {label} \\* ARABIC ", str(registry[key])):
            paragraph.append(run)
        paragraph.append(parse_xml(f'<w:bookmarkEnd {W_NS} w:id="{bid}"/>'))
        paragraph.append(self._run(". ", False, False))
        for run in self._inline_runs(_x(title)):
            paragraph.append(run)
        return paragraph

    # ---- tables -------------------------------------------------------------------

    def _b_table(self, block: dict) -> list:
        out = []
        if block.get("caption_id"):
            out.append(self._caption("table", block["caption_id"], block["title"]))
        out.append(self._table_xml(block))
        out.append(parse_xml(f'<w:p {W_NS}><w:pPr><w:spacing w:before="0" w:after="120"/></w:pPr></w:p>'))
        return out

    def _minimum_widths(self, block: dict) -> list[int]:
        """Width needed so the longest word in each column is never split."""
        columns = block["columns"]
        longest = [max((len(w) for w in str(c["label"]).split()), default=1) for c in columns]
        for row in block["rows"]:
            if isinstance(row, dict) and "span" in row:
                continue
            values = row["cells"] if isinstance(row, dict) else row
            for i, value in enumerate(values[: len(columns)]):
                text = value.get("text", "") if isinstance(value, dict) else value
                word = max((len(w) for w in str(text or "").split()), default=0)
                longest[i] = max(longest[i], min(word, 16))
        return [length * 128 + 190 for length in longest]

    def _widths(self, block: dict) -> list[int]:
        columns = block["columns"]
        weights = [float(c.get("width", 1)) for c in columns]
        total = sum(weights)
        widths = [int(TEXT_WIDTH * w / total) for w in weights]
        minimum = self._minimum_widths(block)
        if sum(minimum) <= TEXT_WIDTH:
            for _ in range(len(columns) + 1):
                short = sum(max(0, m - w) for m, w in zip(minimum, widths))
                if not short:
                    break
                spare = [max(0, w - m) for m, w in zip(minimum, widths)]
                available = sum(spare)
                if not available:
                    break
                widths = [
                    max(w, m) if w < m else w - int(short * s / available)
                    for w, m, s in zip(widths, minimum, spare)
                ]
        widest = widths.index(max(widths))
        widths[widest] += TEXT_WIDTH - sum(widths)
        return widths

    def _cell(self, value: Any, width: int, *, span: int = 1, align: str = "left", bold: bool = False, fill: str | None = None) -> str:
        if isinstance(value, dict):
            fill = value.get("fill", fill)
            align = value.get("align", align)
            bold = value.get("bold", bold)
            value = value.get("text", "")
        tcpr = f'<w:tcW w:w="{width}" w:type="dxa"/>'
        if span > 1:
            tcpr += f'<w:gridSpan w:val="{span}"/>'
        if fill:
            tcpr += f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>'
        tcpr += '<w:vAlign w:val="center"/>'
        paragraphs = str(value if value is not None else "").split("\n")
        body = ""
        for text in paragraphs:
            runs = ""
            b = "<w:rPr><w:b/><w:bCs/></w:rPr>" if bold else ""
            if text:
                runs = f'<w:r>{b}<w:t xml:space="preserve">{_x(text)}</w:t></w:r>'
            body += f'<w:p><w:pPr><w:pStyle w:val="ReportTableCell"/><w:jc w:val="{align}"/></w:pPr>{runs}</w:p>'
        return f"<w:tc><w:tcPr>{tcpr}</w:tcPr>{body}</w:tc>"

    def _table_xml(self, block: dict):
        columns = block["columns"]
        widths = self._widths(block)
        grid ="".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
        border = 'w:val="single" w:sz="4" w:space="0" w:color="000000"'
        head = "".join(
            self._cell(c["label"], widths[i], align="center", bold=True, fill="D9D9D9")
            for i, c in enumerate(columns)
        )
        rows = (
            f'<w:tr><w:trPr><w:cantSplit/><w:tblHeader/></w:trPr>{head}</w:tr>'
        )
        for row in block["rows"]:
            if isinstance(row, dict) and "span" in row:
                cell = self._cell(row["span"], TEXT_WIDTH, span=len(columns), bold=True, fill="F2F2F2")
                rows += f"<w:tr><w:trPr><w:cantSplit/></w:trPr>{cell}</w:tr>"
                continue
            bold = False
            values = row
            if isinstance(row, dict):
                bold = row.get("bold", False)
                values = row["cells"]
            if len(values) != len(columns):
                raise BuildError(f"Row has {len(values)} cells, expected {len(columns)}: {values}")
            cells = "".join(
                self._cell(v, widths[i], align=columns[i].get("align", "left"), bold=bold)
                for i, v in enumerate(values)
            )
            rows += f"<w:tr><w:trPr><w:cantSplit/></w:trPr>{cells}</w:tr>"
        return parse_xml(
            f'<w:tbl {W_NS}><w:tblPr><w:tblW w:w="{TEXT_WIDTH}" w:type="dxa"/><w:jc w:val="center"/>'
            f"<w:tblBorders><w:top {border}/><w:left {border}/><w:bottom {border}/><w:right {border}/>"
            f"<w:insideH {border}/><w:insideV {border}/></w:tblBorders><w:tblLayout w:type=\"fixed\"/>"
            '<w:tblCellMar><w:left w:w="90" w:type="dxa"/><w:right w:w="90" w:type="dxa"/></w:tblCellMar>'
            '<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="1" w:lastColumn="0" '
            f'w:noHBand="0" w:noVBand="1"/></w:tblPr><w:tblGrid>{grid}</w:tblGrid>{rows}</w:tbl>'
        )

    # ---- figures ------------------------------------------------------------------

    def _image_size(self, raw: bytes, requested: float | None) -> tuple[Emu, Emu]:
        with Image.open(io.BytesIO(raw)) as image:
            px_w, px_h = image.size
            dpi = image.info.get("dpi", (96, 96))
        dpi_x = float(dpi[0]) if dpi and dpi[0] else 96.0
        if dpi_x < 72:
            dpi_x = 96.0
        natural = px_w / dpi_x
        width = requested if requested else natural  # never upscale small images on their own
        width = min(width, TEXT_WIDTH_IN)
        height = width * px_h / px_w
        if height > MAX_IMAGE_HEIGHT_IN:
            height = MAX_IMAGE_HEIGHT_IN
            width = height * px_w / px_h
        return Inches(width), Inches(height)

    def _b_figure(self, block: dict) -> list:
        raw = block.get("data")
        if raw is None:
            raw = Path(block["image"]).read_bytes()
        width, height = self._image_size(raw, block.get("width_in"))
        holder = self.document.add_paragraph()
        holder.add_run().add_picture(io.BytesIO(raw), width=width, height=height)
        element = holder._p
        self.body.remove(element)
        style = parse_xml(f'<w:pStyle {W_NS} w:val="ReportFigure"/>')
        element.get_or_add_pPr().insert(0, style)
        alt = block.get("alt") or block.get("title") or ""
        for doc_pr in element.iter("{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}docPr"):
            doc_pr.set("descr", alt)
        out = [element]
        if block.get("caption_id"):
            out.append(self._caption("figure", block["caption_id"], block["title"]))
        elif block.get("title"):
            out.append(self._para("ReportCaptionFigure", _x(block["title"])))
        return out


def build_report(facts: dict, parts: dict[str, list[dict]], options: BuildOptions | None = None, master: Path = MASTER) -> BuildResult:
    return Builder(facts, parts, options or BuildOptions(), master).build()


FRONT_PAGES = {"prelim.cover", "prelim.title", "prelim.approval"}


def _build_front_page(facts: dict, parts: dict, part_key: str, options: BuildOptions, master: Path) -> bytes:
    """Keep only the cover, the title page, or the approval sheet from a built report."""
    built = Builder(facts, parts, options, master).build()
    document = Document(io.BytesIO(built.data))
    body = document.element.body
    elements = list(body)
    texts = ["".join(t.text or "" for t in e.iter(qn("w:t"))) for e in elements]
    cover_end = next(i for i, e in enumerate(elements) if e.find(qn("w:pPr") + "/" + qn("w:sectPr")) is not None)
    approval = texts.index("APPROVAL SHEET")
    acknowledgment = texts.index("ACKNOWLEDGMENT")
    keep = {
        "prelim.cover": range(0, cover_end + 1),
        "prelim.title": range(cover_end + 1, approval),
        "prelim.approval": range(approval, acknowledgment),
    }[part_key]
    if part_key == "prelim.cover":
        # The cover's section settings become the document's final section settings.
        final = body.find(qn("w:sectPr"))
        ppr = elements[cover_end].find(qn("w:pPr"))
        cover_sect = ppr.find(qn("w:sectPr"))
        ppr.remove(cover_sect)
        body.replace(final, cover_sect)
    for index, element in enumerate(elements):
        if index not in keep and element.tag != qn("w:sectPr"):
            body.remove(element)
    settings = document.settings.element
    update = settings.find(qn("w:updateFields"))
    if update is not None:
        settings.remove(update)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def build_single_part(facts: dict, parts: dict[str, list[dict]], part_key: str, options: BuildOptions | None = None, master: Path = MASTER) -> bytes:
    """Build the complete report for correct numbering, then keep only one part."""
    options = options or BuildOptions()
    if part_key in FRONT_PAGES:
        return _build_front_page(facts, parts, part_key, options, master)
    builder = Builder(facts, parts, options, master)
    anchors = builder.anchors()
    target = next((el for key, el in anchors if key == part_key), None)
    marker = "RRG_KEEP_MARK"
    if target is None:
        raise BuildError(f"No such part: {part_key}")
    heading = target.getprevious()
    start_mark = parse_xml(f'<w:p {W_NS}><w:r><w:t>{marker}_S</w:t></w:r></w:p>')
    end_mark = parse_xml(f'<w:p {W_NS}><w:r><w:t>{marker}_E</w:t></w:r></w:p>')
    (heading if heading is not None and builder._is_heading(heading) else target).addprevious(start_mark)
    target.addnext(end_mark)
    result = builder.build()
    document = Document(io.BytesIO(result.data))
    new_body = document.element.body
    keep = False
    for element in list(new_body):
        if element.tag == qn("w:sectPr"):
            continue
        text = "".join(t.text or "" for t in element.iter(qn("w:t")))
        if text == marker + "_S":
            keep = True
            new_body.remove(element)
            continue
        if text == marker + "_E":
            keep = False
            new_body.remove(element)
            continue
        if not keep:
            new_body.remove(element)
    settings = document.settings.element
    update = settings.find(qn("w:updateFields"))
    if update is not None:
        settings.remove(update)
    for paragraph in new_body.iter(qn("w:p")):
        ppr = paragraph.find(qn("w:pPr"))
        if ppr is not None and ppr.find(qn("w:pageBreakBefore")) is not None:
            ppr.remove(ppr.find(qn("w:pageBreakBefore")))
            break
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()
