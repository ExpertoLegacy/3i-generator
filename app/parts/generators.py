"""Offline generators. Each returns {"before": [...], "html": str, "after": [...]}.

``html`` is editable prose. ``before`` and ``after`` are structured blocks (tables, formulas)
that are rebuilt from the inputs. No generator invents content: it only arranges what the
student entered, plus fixed text that the template itself supplies.
"""
from __future__ import annotations

import json
import math
import re
from html import escape
from typing import Callable

from app.docbuild.standard import (
    DEFAULT_RESEARCH_QUESTIONS,
    GANTT_ACTIVITIES,
    GANTT_PERIODS,
    ISO_2011,
    ISO_RENAMED,
    iso_characteristics,
    iso_table,
    statistics_blocks,
    verbal_interpretation,
)
from app.session import Session
from app.sources import apa

from . import spec as spec_module


# ---- defaults offered in the form ------------------------------------------------------

DEFAULT_QUESTIONS = [
    ("problems", "What are the problems encountered in the current {process} of {locale}?", ""),
    ("features", "What features and modules should the system have to address these problems in terms of:", "{modules}"),
    ("development", "How can the system be designed and developed using the {dev_model}?", ""),
    ("acceptability", "What is the level of acceptability of the developed system as evaluated by IT experts and end-users in terms of the ISO/IEC 25010 software quality characteristics:", "functional suitability;\nperformance efficiency;\ncompatibility;\nusability;\nreliability;\nsecurity;\nmaintainability; and\nportability?"),
    ("difference", "Is there a significant difference between the evaluations of the IT experts and the end-users?", ""),
    ("enhancements", "What enhancements can be proposed based on the results of the evaluation?", ""),
]

GATHERING_STEPS = """Secured a letter of permission from the school principal or office head to conduct the study ({{ref:appendix:permission}}).
Conducted interviews and observation to gather the system requirements.
Designed, developed, and tested the system following the chosen software development model.
Had the research instrument validated by experts.
Obtained the informed consent of the respondents, and parental consent and assent for respondents below 18 years old ({{ref:appendix:consent}}).
Demonstrated the system and let the respondents use it by performing task scenarios.
Administered the evaluation questionnaire (printed or through an online form).
Retrieved, tallied, and analyzed the responses using the statistical tools described below."""

FACTORIES: dict[str, Callable] = {
    "research_questions": lambda: [{"question": q, "subitems": s, "rq_id": i} for i, q, s in DEFAULT_QUESTIONS],
    "gathering_steps": lambda: GATHERING_STEPS,
    "iso_rows": lambda: [{"characteristic": name, "experts": "", "users": ""} for name, _, _ in ISO_2011],
    "gantt_rows": lambda: [{"activity": name} for name, _ in GANTT_ACTIVITIES],
}


def default_inputs(part: dict) -> dict:
    values: dict = {}
    for field in part["inputs"]:
        if "default_factory" in field:
            values[field["id"]] = FACTORIES[field["default_factory"]]()
        elif "default_rows" in field:
            values[field["id"]] = [dict(r) for r in field["default_rows"]]
        elif "default" in field:
            values[field["id"]] = field["default"]
    return values


def inputs_for(session: Session, key: str) -> dict:
    part = spec_module.part(key)
    merged = default_inputs(part) if part else {}
    merged.update(session.inputs(key))
    if key == "ch4.acceptability":
        merged["scores"] = _scores_for_edition(merged.get("scores") or [], iso_edition(session))
    return merged


def iso_edition(session: Session) -> str:
    return session.inputs("setup.options").get("iso_edition") or "2011"


def _scores_for_edition(rows: list[dict], edition: str) -> list[dict]:
    """Match the grid to the chosen ISO edition, keeping scores for characteristics that were renamed."""
    by_name = {row.get("characteristic"): row for row in rows}
    aliases = {**ISO_RENAMED, **{new: old for old, new in ISO_RENAMED.items()}}
    output = []
    for name, _, _ in iso_characteristics(edition):
        row = by_name.get(name) or by_name.get(aliases.get(name)) or {}
        output.append({"characteristic": name, "experts": row.get("experts", ""), "users": row.get("users", "")})
    return output


# ---- shared facts ----------------------------------------------------------------------


def split_title(title: str, max_chars: int = 42) -> list[str]:
    """Break a title into centered lines, longest first, for the inverted pyramid."""
    words = title.split()
    lines: list[str] = []
    limit = max_chars
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > limit and current:
            lines.append(current)
            current = word
            limit = max(24, limit - 4)
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def join_names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    if len(names) == 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + ", and " + names[-1]


def build_facts(session: Session) -> dict:
    school = inputs_for(session, "setup.school")
    people = inputs_for(session, "setup.people")
    system = inputs_for(session, "setup.system")
    title = (inputs_for(session, "prelim.title").get("title") or "").strip()
    if not title and system.get("system_name"):
        title = default_title(system)
    names = [p.get("name", "").strip() for p in people.get("proponents", []) if p.get("name", "").strip()]
    facts = {
        "subject": school.get("subject"),
        "subject_upper": (school.get("subject") or "").upper(),
        "research_theme": school.get("research_theme"),
        "strand_section": " – ".join(v for v in (people.get("strand") or people.get("strand_section"), people.get("section")) if v),
        "school_name": school.get("school_name"),
        "school_address": school.get("school_address"),
        "title": title.upper(),
        "title_lines": split_title(title.upper()),
        "proponents_upper": [n.upper() for n in names],
        "proponents_joined": join_names(names),
        "adviser_upper": (school.get("adviser") or "").upper(),
        "month_year": people.get("month_year"),
        "panel_chair_upper": (people.get("panel_chair") or "").upper(),
        "panel_member_1_upper": (people.get("panel_member_1") or "").upper(),
        "panel_member_2_upper": (people.get("panel_member_2") or "").upper(),
        "principal_upper": (school.get("principal") or "").upper(),
        "principal_title": school.get("principal_title"),
    }
    facts["_system"] = system
    return facts


def default_title(system: dict) -> str:
    return (
        f"{system.get('system_name','')}: A {(system.get('platform') or '').capitalize()} "
        f"{system.get('system_type','')} for {system.get('users','')} of {system.get('locale','')}"
    )


def fill(text: str, system: dict) -> str:
    modules = [m.strip() for m in (system.get("modules") or "").splitlines() if m.strip()]
    if modules:
        formatted = [m + ";" for m in modules]
        if len(formatted) > 1:
            formatted[-2] = formatted[-2] + " and"
            formatted[-1] = formatted[-1][:-1] + "?"
        module_text = "\n".join(formatted)
    else:
        module_text = ""
    model = system.get("dev_model") or "chosen software development"
    values = {
        "process": system.get("process") or "process", "locale": system.get("locale") or "the locale",
        "dev_model": model if model.lower().endswith("model") else f"{model} model", "modules": module_text,
    }
    return re.sub(r"\{(process|locale|dev_model|modules)\}", lambda m: values[m.group(1)], text)


def sources_of(session: Session) -> list[dict]:
    return [r for r in inputs_for(session, "ch2.sources").get("sources", []) if (r.get("title") or "").strip()]


def hypothesis_on(session: Session) -> bool:
    return inputs_for(session, "setup.options").get("hypothesis", "Yes") != "No" and "ch1.hypothesis" not in not_necessary(session)


def research_question_ids(session: Session) -> list[str]:
    ids = []
    for index, item in enumerate(inputs_for(session, "ch1.sop").get("questions", []), start=1):
        rq_id = item.get("rq_id") or f"q{index}"
        if rq_id == "difference" and not hypothesis_on(session):
            continue
        ids.append(rq_id)
    return ids or list(DEFAULT_RESEARCH_QUESTIONS)


def not_necessary(session: Session) -> set[str]:
    """Optional parts the students decided their study does not need."""
    return set(json.loads(session.get_meta("not_necessary") or "[]"))


def set_not_necessary(session: Session, key: str, skip: bool) -> None:
    chosen = not_necessary(session)
    (chosen.add if skip else chosen.discard)(key)
    session.set_meta("not_necessary", json.dumps(sorted(chosen)))


GATED_TABS = ("ch1", "ch2", "ch3", "ch4", "ch5")


def gate_order() -> list[dict]:
    """Parts that must be approved in order. The Source Library is a tool, so it is never locked."""
    return [p for tab in spec_module.tabs() if tab["id"] in GATED_TABS for p in tab["parts"] if not p.get("no_output")]


def locks(session: Session) -> dict[str, str]:
    """Locked parts, with the part to finish first.

    A part already started stays open, so reopening an earlier part for panel revisions
    does not lock students out of their later work. The final report still needs every approval.
    """
    status = session.status()
    disabled = disabled_parts(session)
    waiting_for = None
    result: dict[str, str] = {}
    for part in gate_order():
        key = part["key"]
        if waiting_for and key not in disabled and not session.draft(key):
            result[key] = waiting_for
        if key not in disabled and status.get(key) != "reviewed" and not waiting_for:
            waiting_for = key
    return result


def disabled_parts(session: Session) -> set[str]:
    disabled = set(inputs_for(session, "setup.options").get("appendices_off", []) or []) | not_necessary(session)
    if not hypothesis_on(session):
        disabled |= {"ch1.hypothesis", "ch4.difference"}
    return disabled


# ---- helpers ---------------------------------------------------------------------------


def paragraphs(text: str | None) -> str:
    if not text or not text.strip():
        return ""
    return "".join(f"<p>{escape(p.strip(), quote=False)}</p>" for p in re.split(r"\n\s*\n|\n", text) if p.strip())


def lines(text: str | None) -> list[str]:
    return [l.strip() for l in (text or "").splitlines() if l.strip()]


def ordered(items: list[str]) -> str:
    if not items:
        return ""
    # Tokens such as {{ref:...}} survive escaping because braces are not escaped.
    return "<ol>" + "".join(f"<li>{escape(i, quote=False)}</li>" for i in items) + "</ol>"


def result(html: str = "", before: list | None = None, after: list | None = None) -> dict:
    return {"html": html, "before": before or [], "after": after or []}


def grid_rows(rows: list[dict] | None, required: str) -> list[dict]:
    return [r for r in (rows or []) if (r.get(required) or "").strip()]


# ---- generators ------------------------------------------------------------------------


def assemble(ctx: "Context") -> dict:
    html = ""
    for field in ctx.part["inputs"]:
        value = ctx.inputs.get(field["id"])
        if field["type"] == "textarea":
            html += paragraphs(value)
        elif field["type"] == "repeater":
            lead = field.get("lead_field")
            heading = field.get("heading_field")
            for row in value or []:
                texts = [
                    (row.get(f["id"]) or "").strip() for f in field["fields"]
                    if f["id"] not in (lead, heading) and f.get("type") != "hidden"
                ]
                body = " ".join(t for t in texts if t)
                if heading and (row.get(heading) or "").strip():
                    html += f"<h3>{escape(row[heading].strip(), quote=False)}</h3>"
                if lead and (row.get(lead) or "").strip():
                    html += f"<p><strong>{escape(row[lead].strip(), quote=False)}.</strong> {escape(body, quote=False)}</p>"
                elif body:
                    html += paragraphs(body)
    return result(html)


def g_abstract(ctx: "Context") -> dict:
    html = paragraphs(ctx.inputs.get("abstract"))
    keywords = (ctx.inputs.get("keywords") or "").strip()
    if keywords:
        html += f"<p><strong>Keywords:</strong> {escape(keywords, quote=False)}</p>"
    return {**result(html), "indent": False}


def g_sop(ctx: "Context") -> dict:
    s = ctx.system
    intro = (
        f"<p>This study aims to design, develop, and evaluate {escape(s.get('system_name') or '')}, a "
        f"{escape(s.get('platform') or '')} {escape(s.get('system_type') or '')} for "
        f"{escape(s.get('users') or '')} of {escape(s.get('locale') or '')}. "
        "Specifically, it seeks to answer the following questions:</p>"
    )
    items = []
    for row in ctx.inputs.get("questions") or []:
        if row.get("rq_id") == "difference" and not hypothesis_on(ctx.session):
            continue
        question = fill(row.get("question") or "", s).strip()
        subitems = fill(row.get("subitems") or "", s)
        if iso_edition(ctx.session) == "2023":
            subitems = subitems.replace("usability;", "interaction capability;").replace("maintainability; and\nportability?", "maintainability;\nflexibility; and\nsafety?")
        if question:
            items.append({"text": question, "children": lines(subitems)})
    return result(intro, after=[{"type": "list", "items": items}] if items else [])


def g_objectives(ctx: "Context") -> dict:
    name = escape(ctx.system.get("system_name") or "the system")
    intro = f"<p>The general objective of this study is to design, develop, and evaluate {name}. Specifically, it aims to:</p>"
    return result(intro + ordered(lines(ctx.inputs.get("objectives"))))


def g_hypothesis(ctx: "Context") -> dict:
    name = escape(ctx.system.get("system_name") or "the system")
    alpha = escape(ctx.inputs.get("alpha") or "0.05")
    return result(
        f"<p><strong>Ho:</strong> There is no significant difference between the evaluations of the IT experts "
        f"and the end-users on the level of acceptability of {name} at the {alpha} level of significance.</p>"
    )


def g_terms(ctx: "Context") -> dict:
    rows = sorted(grid_rows(ctx.inputs.get("terms"), "term"), key=lambda r: r["term"].lower())
    return result("".join(
        f"<p><strong>{escape(r['term'].strip(), quote=False)}.</strong> {escape((r.get('definition') or '').strip(), quote=False)}</p>"
        for r in rows
    ))


def g_studies(ctx: "Context") -> dict:
    html = ""
    for title, key in (("Foreign Studies", "foreign"), ("Local Studies", "local")):
        html += f"<h3>{title}</h3>" + paragraphs(ctx.inputs.get(key))
    return result(html)


def g_synthesis(ctx: "Context") -> dict:
    i = ctx.inputs
    html = paragraphs(i.get("synthesis"))
    rows = grid_rows(i.get("matrix"), "feature")
    after = []
    if rows:
        center = {"align": "center"}
        after.append({
            "type": "table", "caption_id": "features",
            "title": "Comparison of Features of Related Systems and the Proposed System",
            "columns": [
                {"label": "Feature", "width": 3},
                {"label": i.get("system_a") or "System A", "width": 1.2, **center},
                {"label": i.get("system_b") or "System B", "width": 1.2, **center},
                {"label": i.get("system_c") or "System C", "width": 1.2, **center},
                {"label": ctx.system.get("system_name") or "Proposed System", "width": 1.2, **center},
            ],
            "rows": [[r["feature"], r.get("a", ""), r.get("b", ""), r.get("c", ""), r.get("own", "")] for r in rows],
        })
    return result(html, after=after)


def g_phase(ctx: "Context") -> dict:
    return result(paragraphs(ctx.inputs.get("text")), before=[{"type": "heading3", "text": ctx.part["phase_title"]}])


def g_subheading(ctx: "Context") -> dict:
    return {**assemble(ctx), "before": [{"type": "heading3", "text": ctx.part["subheading"]}]}


def g_requirements(ctx: "Context") -> dict:
    i = ctx.inputs
    after = []
    hardware = grid_rows(i.get("hardware"), "component")
    if hardware:
        after.append({"type": "table", "caption_id": "hardware", "title": "Hardware Requirements",
                      "columns": [{"label": "Component"}, {"label": "Used in Development"}, {"label": "Minimum Requirement for Users"}],
                      "rows": [[r["component"], r.get("used", ""), r.get("minimum", "")] for r in hardware]})
    software = grid_rows(i.get("software"), "software")
    if software:
        after.append({"type": "table", "caption_id": "software", "title": "Software Requirements",
                      "columns": [{"label": "Software"}, {"label": "Purpose", "width": 2}, {"label": "Version"}],
                      "rows": [[r["software"], r.get("purpose", ""), r.get("version", "")] for r in software]})
    return result(paragraphs(i.get("intro")), after=after)


def to_number(value) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def percentage(sample, population) -> str:
    s, p = to_number(sample), to_number(population)
    return f"{s / p * 100:.2f}%" if s is not None and p else ""


def g_population(ctx: "Context") -> dict:
    rows = grid_rows(ctx.inputs.get("groups"), "group")
    center = {"align": "center"}
    table_rows: list = [[r["group"], r.get("population", ""), r.get("sample", ""), percentage(r.get("sample"), r.get("population"))] for r in rows]
    populations = [to_number(r.get("population")) for r in rows]
    samples = [to_number(r.get("sample")) for r in rows]
    if rows and all(v is not None for v in populations + samples):
        total_p, total_s = sum(populations), sum(samples)
        table_rows.append({"bold": True, "cells": ["Total", f"{total_p:g}", f"{total_s:g}", percentage(total_s, total_p)]})
    after = [{"type": "table", "caption_id": "respondents", "title": "Distribution of Respondents",
              "columns": [{"label": "Respondent Group", "width": 2}, {"label": "Population (N)", **center}, {"label": "Sample (n)", **center}, {"label": "Percentage", **center}],
              "rows": table_rows}] if rows else []
    return result(paragraphs(ctx.inputs.get("text")), after=after)


def slovin(population: float, margin: float) -> int:
    return math.ceil(population / (1 + population * margin ** 2))


def g_sampling(ctx: "Context") -> dict:
    html = paragraphs(ctx.inputs.get("text"))
    after = []
    population = to_number(ctx.inputs.get("slovin_population"))
    margin = to_number(ctx.inputs.get("slovin_margin"))
    if population and margin:
        n = slovin(population, margin)
        after = [
            {"type": "formula", "text": "n = N / (1 + Ne²)"},
            {"type": "note", "text": f"where n = sample size; N = population ({population:g}); e = margin of error ({margin:g}). The computed sample size is {n}."},
        ]
    return result(html, after=after)


def g_instrument(ctx: "Context") -> dict:
    html = paragraphs(ctx.inputs.get("text"))
    alpha = to_number(ctx.inputs.get("alpha"))
    if alpha is not None:
        verdict = "meets the acceptable level of 0.70 or higher" if alpha >= 0.70 else "is below the acceptable level of 0.70"
        html += f"<p>Pilot testing of the instrument yielded a Cronbach's alpha of {alpha:.2f}, which {verdict}.</p>"
    return result(html, after=[iso_table(iso_edition(ctx.session))])


def g_numbered(ctx: "Context") -> dict:
    return result(ordered(lines(ctx.inputs.get("items"))))


def g_statistics(ctx: "Context") -> dict:
    blocks = statistics_blocks(include_difference=hypothesis_on(ctx.session))
    software = (ctx.inputs.get("software") or "").strip()
    if software:
        blocks.append({"type": "paragraph", "text": f"The data were processed using {software}."})
    return result("", after=blocks)


def g_problems(ctx: "Context") -> dict:
    rows = grid_rows(ctx.inputs.get("mapping"), "problem")
    after = [{"type": "table", "caption_id": "problems", "title": "Identified Problems and Corresponding System Features",
              "columns": [{"label": "Problem Identified"}, {"label": "System Feature / Module That Addresses It"}],
              "rows": [[r["problem"], r.get("feature", "")] for r in rows]}] if rows else []
    return result(paragraphs(ctx.inputs.get("findings")), after=after)


def test_case_table(rows: list[dict], caption: bool) -> dict:
    table = {
        "type": "table",
        "columns": [{"label": "Test Case ID", "width": 0.8}, {"label": "Module", "width": 0.9}, {"label": "Test Scenario", "width": 1.8},
                    {"label": "Expected Result", "width": 1.6}, {"label": "Actual Result", "width": 1.6}, {"label": "Status", "width": 0.7, "align": "center"}],
        "rows": [[r.get("id", ""), r.get("module", ""), r.get("scenario", ""), r.get("expected", ""), r.get("actual", ""), r.get("status", "")] for r in rows],
    }
    if caption:
        table.update(caption_id="tests", title="Sample Test Cases and Results")
    return table


def g_testing(ctx: "Context") -> dict:
    rows = grid_rows(ctx.inputs.get("cases"), "scenario")
    passed = sum(1 for r in rows if (r.get("status") or "").lower() == "pass")
    html = ""
    if rows:
        html = f"<p>A total of {len(rows)} test cases were conducted, of which {passed} passed, for a pass rate of {passed / len(rows) * 100:.2f}%.</p>"
    html += paragraphs(ctx.inputs.get("text"))
    return result(html, after=[test_case_table(rows[:10], True)] if rows else [])


def g_test_cases_appendix(ctx: "Context") -> dict:
    rows = grid_rows(inputs_for(ctx.session, "ch4.testing").get("cases"), "scenario")
    return result("", after=[test_case_table(rows, False)] if rows else [])


def g_acceptability(ctx: "Context") -> dict:
    rows = ctx.inputs.get("scores") or []
    center = {"align": "center"}
    name = ctx.system.get("system_name") or "the System"

    def pair(value):
        number = to_number(value)
        if number is None or not 1 <= number <= 5:
            return ["—", "—"]
        return [f"{number:.2f}", verbal_interpretation(number)]

    table_rows: list = [[r.get("characteristic", ""), *pair(r.get("experts")), *pair(r.get("users"))] for r in rows]
    experts = [n for n in (to_number(r.get("experts")) for r in rows) if n is not None]
    users = [n for n in (to_number(r.get("users")) for r in rows) if n is not None]
    if experts or users:
        table_rows.append({"bold": True, "cells": [
            "Overall Weighted Mean",
            *(pair(sum(experts) / len(experts)) if experts else ["—", "—"]),
            *(pair(sum(users) / len(users)) if users else ["—", "—"]),
        ]})
    after = [{"type": "table", "caption_id": "acceptability", "title": f"Summary of the Level of Acceptability of {name}",
              "columns": [{"label": "Characteristic", "width": 2.2}, {"label": "IT Experts WM", **center}, {"label": "Verbal Interpretation", "width": 1.4, **center}, {"label": "End-Users WM", **center}, {"label": "Verbal Interpretation", "width": 1.4, **center}],
              "rows": table_rows}] if rows else []
    return result(paragraphs(ctx.inputs.get("text")), after=after)


def g_difference(ctx: "Context") -> dict:
    i = ctx.inputs
    p = to_number(i.get("p"))
    decision, meaning = "", ""
    alpha = to_number(inputs_for(ctx.session, "ch1.hypothesis").get("alpha")) or 0.05
    if p is not None:
        reject = p < alpha
        decision = "Reject" if reject else "Failed to reject"
        meaning = "Significant" if reject else "Not significant"
    center = {"align": "center"}
    table = {"type": "table", "caption_id": "difference", "title": "Test of Difference Between the Evaluations of IT Experts and End-Users",
             "columns": [{"label": n, **center} for n in ("Group", "Mean", "SD", "t-value", "p-value", "Decision on Ho", "Interpretation")],
             "rows": [["IT Experts", i.get("experts_mean", ""), i.get("experts_sd", ""), i.get("t", ""), i.get("p", ""), decision, meaning],
                      ["End-Users", i.get("users_mean", ""), i.get("users_sd", ""), "", "", "", ""]]}
    return result(paragraphs(i.get("text")), before=[table])


def g_summary(ctx: "Context") -> dict:
    return result(paragraphs(ctx.inputs.get("intro")) + ordered(lines(ctx.inputs.get("items"))))


def g_references(ctx: "Context") -> dict:
    keys = [p["key"] for p in spec_module.all_parts() if p["key"] != "refs.list"]
    text = " ".join(re.sub(r"<[^>]+>", " ", (ctx.session.draft(k) or {}).get("html", "")) for k in keys)
    entries = [apa.format_reference(s) for s in sources_of(ctx.session) if apa.is_cited(s, text)]
    for entry in lines(ctx.inputs.get("entries")):
        entries.append(re.sub(r"\*(.+?)\*", r"<i>\1</i>", escape(entry, quote=False)))
    entries.sort(key=lambda e: re.sub(r"<[^>]+>", "", e).lower())
    return result("", after=[{"type": "reference", "html": e} for e in entries])


def g_instrument_appendix(ctx: "Context") -> dict:
    rows = grid_rows(inputs_for(ctx.session, "ch3.instrument").get("indicators"), "indicator")
    groups: dict[str, list[str]] = {}
    for row in rows:
        groups.setdefault(row.get("characteristic") or "General", []).append(row["indicator"])
    center = {"align": "center", "width": 0.6}
    table_rows: list = []
    for letter, (name, indicators) in zip("ABCDEFGHIJKLMNOP", groups.items()):
        table_rows.append({"span": f"{letter}. {name}"})
        for number, text in enumerate(indicators, start=1):
            table_rows.append([f"{number}. {text}", "", "", "", "", ""])
    blocks: list = [{"type": "paragraph", "indent": False, "html": "<b>Directions:</b> Please read each statement carefully and put a check (✓) in the column that best describes your evaluation of the system. 5 – Strongly Agree; 4 – Agree; 3 – Neutral; 2 – Disagree; 1 – Strongly Disagree."}]
    if table_rows:
        blocks.append({"type": "table", "columns": [{"label": "Indicators", "width": 6}, *[{"label": str(n), **center} for n in (5, 4, 3, 2, 1)]], "rows": table_rows})
    blocks.append({"type": "note", "html": "<b>Comments and Suggestions:</b> ________________________________________"})
    return result("", after=blocks)


def g_gantt(ctx: "Context") -> dict:
    center = {"align": "center", "width": 0.7}
    table_rows = []
    for row in ctx.inputs.get("activities") or []:
        table_rows.append([row.get("activity", ""), *[
            {"text": "", "fill": "808080"} if (row.get(f"p{i}") or "").strip() else "" for i in range(len(GANTT_PERIODS))
        ]])
    return result("", after=[
        {"type": "table", "columns": [{"label": "Activity", "width": 3}, *[{"label": p, **center} for p in GANTT_PERIODS]], "rows": table_rows}
    ])


def g_revision(ctx: "Context") -> dict:
    rows = grid_rows(ctx.inputs.get("rows"), "comment")
    center = {"align": "center"}
    return result("", after=[{"type": "table", "columns": [{"label": "No.", "width": 0.5, **center}, {"label": "Panel Member", "width": 1.4}, {"label": "Comments / Suggestions", "width": 2.4}, {"label": "Action Taken", "width": 2.4}, {"label": "Page No.", "width": 0.7, **center}],
                              "rows": [[str(n), r.get("member", ""), r["comment"], r.get("action", ""), r.get("page", "")] for n, r in enumerate(rows, start=1)]}] if rows else [])


GENERATORS: dict[str, Callable[["Context"], dict]] = {
    "abstract": g_abstract, "sop": g_sop, "objectives": g_objectives, "hypothesis": g_hypothesis, "terms": g_terms,
    "studies": g_studies, "synthesis": g_synthesis, "phase": g_phase, "subheading": g_subheading,
    "requirements": g_requirements, "population": g_population, "sampling": g_sampling, "instrument": g_instrument,
    "numbered": g_numbered, "statistics": g_statistics, "problems": g_problems, "testing": g_testing,
    "test_cases_appendix": g_test_cases_appendix, "acceptability": g_acceptability, "difference": g_difference,
    "summary": g_summary, "references": g_references, "instrument_appendix": g_instrument_appendix, "gantt": g_gantt,
    "revision": g_revision,
}


class Context:
    def __init__(self, session: Session, part: dict):
        self.session = session
        self.part = part
        self.inputs = inputs_for(session, part["key"])
        self.system = inputs_for(session, "setup.system")


def generate(session: Session, key: str) -> dict:
    part = spec_module.part(key)
    if part is None:
        raise KeyError(key)
    function = GENERATORS.get(part.get("gen", ""), assemble)
    return function(Context(session, part))
