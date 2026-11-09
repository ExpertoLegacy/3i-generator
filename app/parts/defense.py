"""Defense Prep: a slide outline and likely panel questions, built only from what the report contains."""
from __future__ import annotations

import io
import re
from html import unescape

from docx import Document

from app.docbuild.standard import verbal_interpretation
from app.session import Session

from . import generators as g

MAX_BULLETS = 6


def _sentences(text: str | None, limit: int = 2) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", " ".join((text or "").split()))
    return [p for p in parts if p][:limit]


def _items(session: Session, key: str, field: str) -> list[str]:
    """Prefer the text that will be printed, which AI or the student may have changed."""
    html = (session.draft(key) or {}).get("html", "")
    found = [unescape(re.sub(r"<[^>]+>", " ", i)).strip() for i in re.findall(r"<li[^>]*>(.*?)</li>", html, re.S)]
    return found or g.lines(g.inputs_for(session, key).get(field))


def _slide(title: str, bullets: list[str]) -> dict:
    return {"title": title, "bullets": "\n".join(b for b in bullets[:MAX_BULLETS] if b), "notes": ""}


def _acceptability(session: Session) -> list[str]:
    rows = g.inputs_for(session, "ch4.acceptability").get("scores") or []
    bullets = []
    for column, name in (("experts", "IT experts"), ("users", "End-users")):
        scored = [(g.to_number(r.get(column)), r.get("characteristic", "")) for r in rows]
        scored = [(v, c) for v, c in scored if v is not None]
        if scored:
            overall = sum(v for v, _ in scored) / len(scored)
            highest, lowest = max(scored), min(scored)
            bullets.append(f"{name}: overall weighted mean {overall:.2f} ({verbal_interpretation(overall)})")
            bullets.append(f"{name}: highest {highest[1]} ({highest[0]:.2f}), lowest {lowest[1]} ({lowest[0]:.2f})")
    return bullets


def _testing(session: Session) -> list[str]:
    cases = g.grid_rows(g.inputs_for(session, "ch4.testing").get("cases"), "scenario")
    if not cases:
        return []
    passed = sum(1 for c in cases if (c.get("status") or "").lower() == "pass")
    return [f"{len(cases)} test cases, {passed} passed ({passed / len(cases) * 100:.2f}%)", *_sentences(g.inputs_for(session, "ch4.testing").get("text"), 1)]


def outline(session: Session) -> dict:
    facts = g.build_facts(session)
    system = g.inputs_for(session, "setup.system")
    background = g.inputs_for(session, "ch1.background")
    scope = g.inputs_for(session, "ch1.scope")
    framework = g.inputs_for(session, "ch1.framework")
    modules = g.lines(system.get("modules"))
    roles = g.lines(system.get("roles"))
    related = [r.get("name", "") for r in g.grid_rows(g.inputs_for(session, "ch2.systems").get("systems"), "name")]
    questions = [r.get("question", "") for r in g.inputs_for(session, "ch1.sop").get("questions", [])]
    model = system.get("dev_model") or "the chosen model"
    slides = [
        _slide(facts["title"] or "Title", [*(p.title() for p in facts["proponents_upper"]), facts.get("school_name") or "", facts.get("month_year") or ""]),
        _slide("Background of the Study", _sentences(background.get("context"), 1) + _sentences(background.get("situation"), 2)),
        _slide("Statement of the Problem", [*g.lines(background.get("problems"))[:3], f"The study answers {len(questions)} research questions." if questions else ""]),
        _slide("Objectives", _items(session, "ch1.objectives", "objectives")),
        _slide("Scope and Limitations", _sentences(scope.get("scope"), 2) + _sentences(scope.get("limits"), 2)),
        _slide("Conceptual Framework", [f"Input: {'; '.join(g.lines(framework.get('input')))}", f"Process: {'; '.join(g.lines(framework.get('process')))}", f"Output: {'; '.join(g.lines(framework.get('output')))}"] if framework.get("input") else []),
        _slide("Related Systems and the Gap", [f"Reviewed: {', '.join(related)}" if related else "", *_sentences(g.inputs_for(session, "ch2.synthesis").get("synthesis"), 2)]),
        _slide("Methodology", [*_sentences(g.inputs_for(session, "ch3.design").get("design"), 1), f"Development model: {model}", *_sentences(g.inputs_for(session, "ch3.method.overview").get("model"), 1)]),
        _slide("System Design", [f"Modules: {', '.join(modules)}" if modules else "", f"Users: {', '.join(roles)}" if roles else "", *_sentences(g.inputs_for(session, "ch4.design.architecture").get("text"), 1)]),
        _slide("Live System Demonstration", [*(f"Show: {m}" for m in modules[:4]), "Backup: screen-recorded demo and an offline copy"]),
        _slide("System Testing", _testing(session)),
        _slide("Results: Level of Acceptability", _acceptability(session)),
    ]
    difference = g.inputs_for(session, "ch4.difference")
    if g.hypothesis_on(session) and difference.get("t"):
        slides.append(_slide("Test of Significant Difference", [f"t = {difference.get('t')}, p = {difference.get('p')}", *_sentences(difference.get("text"), 1)]))
    slides.append(_slide("Conclusions", _items(session, "ch5.conclusions", "items")))
    slides.append(_slide("Recommendations", _items(session, "ch5.recommendations", "items")))
    first = modules[0] if modules else "your main module"
    asked = [
        f"How does the {first} module work in the code?",
        "Why did you choose your programming language, framework, and database?",
        f"Why was {model} the right development model for this study?",
        "How did you choose and validate your respondents and questionnaire?",
        "What does your lowest-rated characteristic mean, and what will you do about it?",
        "How does your system protect personal data under RA 10173?",
        "Which of your scope items were fully delivered, and which were not?",
        "What makes your system different from the related systems you reviewed?",
        "What happens if the internet or the database fails during use?",
        "If you repeated the study, what would you change?",
    ]
    return {"slides": slides, "questions": [{"question": q, "answer": ""} for q in asked]}


def to_docx(session: Session) -> bytes:
    values = g.inputs_for(session, "finish.defense")
    document = Document()
    document.add_heading("Defense Prep", 0)
    document.add_heading("Slide outline", 1)
    for number, slide in enumerate(values.get("slides") or [], start=1):
        document.add_heading(f"Slide {number}: {slide.get('title', '')}", 2)
        for line in g.lines(slide.get("bullets")):
            document.add_paragraph(line, style="List Bullet")
        if (slide.get("notes") or "").strip():
            document.add_paragraph(f"Speaker notes: {slide['notes'].strip()}")
    document.add_heading("Likely panel questions", 1)
    for row in values.get("questions") or []:
        document.add_paragraph(row.get("question", ""), style="List Number")
        if (row.get("answer") or "").strip():
            document.add_paragraph(f"Our answer: {row['answer'].strip()}")
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()
