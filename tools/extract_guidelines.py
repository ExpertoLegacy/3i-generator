"""Extract every guide box in the source template into spec/guidelines.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "template" / "source-template.docx"
OUTPUT = ROOT / "spec" / "guidelines.json"

GUIDE_PREFIXES = ("GUIDELINES", "CURRICULUM LINK", "HOW TO USE", "FORMAT", "WRITING", "BEFORE THE ORAL", "DURING AND AFTER")

# Element number of a heading -> part that its guide boxes belong to.
HEADINGS = {
    53: "general.about", 62: "general.format", 69: "prelim.approval", 86: "prelim.acknowledgment",
    90: "prelim.abstract", 95: "prelim.contents", 114: "ch1", 117: "ch1.background", 121: "ch1.sop",
    144: "ch1.objectives", 155: "ch1.hypothesis", 159: "ch1.framework", 166: "ch1.scope",
    170: "ch1.significance", 177: "ch1.terms", 182: "ch2", 187: "ch2.literature", 194: "ch2.studies",
    201: "ch2.systems", 205: "ch2.synthesis", 212: "ch3", 215: "ch3.design", 219: "ch3.method.overview",
    226: "ch3.method.phase1", 230: "ch3.method.phase2", 234: "ch3.method.phase3", 238: "ch3.method.phase4",
    242: "ch3.method.phase5", 246: "ch3.requirements", 255: "ch3.locale", 259: "ch3.population",
    266: "ch3.sampling", 270: "ch3.instrument", 277: "ch3.gathering", 289: "ch3.statistics", 303: "ch3.ethics",
    307: "ch4", 312: "ch4.problems", 319: "ch4.design", 342: "ch4.testing", 349: "ch4.acceptability",
    356: "ch4.difference", 363: "ch4.enhancements", 367: "ch5", 370: "ch5.summary", 379: "ch5.conclusions",
    387: "ch5.recommendations", 394: "refs.list", 401: "app", 404: "app.permission", 408: "app.consent",
    412: "app.instrument", 419: "app.validation", 423: "app.interview", 427: "app.test_cases", 431: "app.gantt",
    437: "app.manual", 441: "app.code", 445: "app.documentation", 449: "app.editing", 453: "app.revision",
    462: "app.cv", 466: "finish.defense",
}
OVERRIDES = {99: "prelim.contents", 106: "prelim.contents"}
TITLE_BOX = 67  # "WRITING YOUR RESEARCH TITLE" belongs to the title page.


def paragraph_text(paragraph) -> str:
    return "".join(t.text or "" for t in paragraph.iter(qn("w:t"))).strip()


def main() -> None:
    body = list(Document(str(SOURCE)).element.body)
    current = "general.about"
    output: dict[str, dict[str, list[str]]] = {}
    total = 0
    for index, element in enumerate(body):
        current = HEADINGS.get(index, OVERRIDES.get(index, current))
        if element.tag != qn("w:tbl"):
            continue
        rows = element.findall(qn("w:tr"))
        if len(rows) != 1 or len(rows[0].findall(qn("w:tc"))) != 1:
            continue
        paragraphs = [paragraph_text(p) for p in element.iter(qn("w:p"))]
        paragraphs = [p for p in paragraphs if p]
        if not paragraphs or not paragraphs[0].startswith(GUIDE_PREFIXES):
            continue
        total += 1
        key = "prelim.title" if index == TITLE_BOX else current
        label = paragraphs[0]
        kind = "curriculum_link" if label.startswith("CURRICULUM LINK") else "guidelines"
        entry = output.setdefault(key, {})
        entry.setdefault(kind, []).extend(paragraphs[1:])
    if total != 70:
        sys.exit(f"Expected 70 guide boxes, found {total}")
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT} with {total} guide boxes in {len(output)} parts")


if __name__ == "__main__":
    main()
