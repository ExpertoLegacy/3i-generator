"""Turn saved drafts into builder blocks, and check a session before export."""
from __future__ import annotations

import re
from datetime import date
from html import unescape

from app.docbuild import BuildOptions, BuildResult, build_report, build_single_part
from app.session import Session
from app.sources import apa

from . import disclosure
from . import generators as g
from . import spec as spec_module

CHECKLIST = [
    ("approval", "Approval Sheet signed by the adviser, panel, and school head", None),
    ("abstract", "Abstract written last and within 150–250 words", "abstract"),
    ("chapters", "Chapters 1–5 revised based on the panel’s suggestions", None),
    ("placeholders", "All guide boxes and bracketed placeholders removed", "placeholders"),
    ("lists", "Table of Contents, List of Tables, and List of Figures updated", None),
    ("references", "References complete and in APA 7th edition; all citations match", None),
    ("appendices", "All required appendices included and properly lettered", None),
    ("revision", "Revision Matrix accomplished and signed", None),
    ("digital", "Digital copy submitted: paper (DOCX and PDF), source code, database file, installer or link, and user manual", None),
    ("similarity", "Similarity or plagiarism check done, if required by the school", None),
    ("printed", "Printed or bound copies prepared as required by the school", None),
]


AI_LABEL = "AI-Generated Concept Mockup"


def _template_text(text: str, session: Session) -> str:
    system = g.inputs_for(session, "setup.system")
    return text.replace("{system}", system.get("system_name") or "the System").replace("{model}", system.get("dev_model") or "Software Development")


def attachment_blocks(session: Session, key: str) -> list[dict]:
    part = spec_module.part(key) or {}
    config = part.get("attach")
    if not config:
        return []
    blocks: list[dict] = []
    short = re.sub(r"\W", "_", key)
    for index, item in enumerate(session.attachments(key)):
        if config.get("consent") and not item["consent"]:
            continue
        data = session.attachment_data(item["id"])[0]
        figure: dict = {"type": "figure", "data": data, "alt": item["caption"] or item["filename"]}
        if config.get("caption_id"):
            if index > 0:
                continue
            drawn_as = (session.diagram(key) or {}).get("kind")
            figure.update(caption_id=config["caption_id"], title=_template_text((config.get("titles") or {}).get(drawn_as, config["title"]), session))
            if config.get("intro"):
                blocks.append({"type": "paragraph", "text": config["intro"]})
        elif config.get("captioned"):
            caption_id = f"{short}_{item['id']}"
            title = item["caption"] or item["filename"]
            if item["ai"] and AI_LABEL.lower() not in title.lower():  # the label cannot be edited away
                title = f"{title} ({AI_LABEL})"
            figure.update(caption_id=caption_id, title=title)
            blocks.append({"type": "paragraph", "text": f"{{{{ref:figure:{caption_id}}}}} shows {title}."})
        elif config.get("caption_text") and item["caption"]:
            figure["title"] = item["caption"]
        blocks.append(figure)
    return blocks


def draft_blocks(session: Session, key: str) -> list[dict] | None:
    draft = session.draft(key)
    blocks: list[dict] = []
    if draft:
        blocks.extend(draft.get("before", []))
        html = (draft.get("html") or "").strip()
        if html and re.sub(r"<[^>]+>", "", html).strip():
            blocks.append({"type": "html", "html": html, "indent": draft.get("indent", True)})
        blocks.extend(draft.get("after", []))
        statement = disclosure.statement(session)
        if statement and key == "ch3.ethics":
            blocks.append({"type": "paragraph", "text": statement})
        if statement and key == "ch3.method.phase3":
            blocks.append({"type": "paragraph", "text": disclosure.DEVELOPMENT_SENTENCE})
    blocks.extend(attachment_blocks(session, key))
    return blocks or None


def collect(session: Session) -> tuple[dict, dict[str, list[dict]], BuildOptions]:
    facts = g.build_facts(session)
    parts: dict[str, list[dict]] = {}
    for key in spec_module.report_keys():
        blocks = draft_blocks(session, key)
        if blocks:
            parts[key] = blocks
    options = BuildOptions(disabled_parts=g.disabled_parts(session), research_questions=g.research_question_ids(session))
    return facts, parts, options


def export_report(session: Session, final: bool) -> BuildResult:
    facts, parts, options = collect(session)
    options.final = final
    for key in options.disabled_parts:
        parts.pop(key, None)
    return build_report(facts, parts, options)


def export_part(session: Session, key: str) -> bytes:
    facts, parts, options = collect(session)
    return build_single_part(facts, parts, key, options)


def plain(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html or ""))).strip()


def master_report(session: Session, built: set[str]) -> bytes:
    """The Word master: Chapter tabs the students have built, using only what they approved."""
    facts, parts, options = collect(session)
    status = session.status()
    tab_of = {p["key"]: p["tab"] for p in spec_module.all_parts()}
    kept = {}
    for key, blocks in parts.items():
        tab = tab_of.get(key)
        approved = status.get(key) == "reviewed" or tab not in g.GATED_TABS
        if tab in built and approved and key not in options.disabled_parts:
            kept[key] = blocks
    options.final = False
    return build_report(facts, kept, options).data


def instrument_document(session: Session) -> bytes:
    """The questionnaire on its own, for conducting the evaluation."""
    facts, parts, options = collect(session)
    fresh = g.generate(session, "app.instrument")
    parts["app.instrument"] = [*fresh.get("before", []), *fresh.get("after", [])]
    options.disabled_parts.discard("app.instrument")
    return build_single_part(facts, parts, "app.instrument", options)


def _item_count(session: Session, key: str, field: str) -> int:
    """Count list items in the text that will be printed, which AI or the student may have changed."""
    html = (session.draft(key) or {}).get("html", "")
    return html.count("<li") if "<li" in html else len(g.lines(g.inputs_for(session, key).get(field)))


def run_checks(session: Session) -> list[dict]:
    """Rule-based checks. Each issue links to the part that needs attention."""
    issues: list[dict] = []
    status = session.status()
    disabled = g.disabled_parts(session)

    def add(key: str, message: str, blocking: bool = True) -> None:
        issues.append({"key": key, "label": (spec_module.part(key) or {}).get("label", key), "message": message, "blocking": blocking})

    facts = g.build_facts(session)
    required = {
        "setup.school": ("school_name", "school_address", "subject", "research_theme", "adviser_upper", "principal_upper"),
        "setup.people": ("strand_section", "month_year", "panel_chair_upper", "panel_member_1_upper", "panel_member_2_upper"),
        "setup.system": ("title",),
    }
    for key, names in required.items():
        for name in names:
            if not facts.get(name):
                add(key, f"Missing: {name.replace('_upper', '').replace('_', ' ')}.")
    if not facts.get("proponents_upper"):
        add("setup.people", "Add at least one proponent.")

    for key in spec_module.report_keys():
        if key in disabled:
            continue
        if not g.spec_module.part(key):
            continue
        if not session.draft(key):
            if key in session.attachment_parts():
                continue  # an uploaded document is complete on its own
            add(key, "This part has not been generated yet.")
            continue
        if status.get(key) != "reviewed":
            add(key, "Read this part, then mark it as reviewed.")
        draft = session.draft(key)
        text = plain(draft.get("html", ""))
        if "[" in text and re.search(r"\[[^\]]{1,60}\]", text):
            add(key, "Replace the bracketed placeholder text.")
        if re.search(r"\b(I|we|our|my)\b", text):
            add(key, "Formal research writing uses the third person. Replace I, we, our, or my.", blocking=False)

    abstract = plain((session.draft("prelim.abstract") or {}).get("html", ""))
    abstract = abstract.split("Keywords:")[0].strip()
    if abstract:
        words = len(abstract.split())
        if not 150 <= words <= 250:
            add("prelim.abstract", f"The abstract has {words} words. It should have 150 to 250.", blocking=False)

    questions = g.research_question_ids(session)
    objectives = _item_count(session, "ch1.objectives", "objectives")
    if objectives and objectives != len(questions):
        add("ch1.objectives", f"You have {objectives} objectives and {len(questions)} research questions. They should match one to one.")
    for key, label in (("ch5.summary", "findings"), ("ch5.conclusions", "conclusions")):
        items = _item_count(session, key, "items")
        if items and items != len(questions):
            add(key, f"You have {items} {label} and {len(questions)} research questions. Give one for each question.")

    for key in spec_module.report_keys():
        found = ((session.draft(key) or {}).get("ai") or {}).get("issues") or []
        if key not in disabled and found:
            add(key, f"The AI draft has {len(found)} items to confirm. Open this part to see them.", blocking=False)

    for key, count in session.open_note_counts().items():
        if key not in disabled:
            add(key, f"{count} adviser note{'s are' if count != 1 else ' is'} still open. Address and resolve {'them' if count != 1 else 'it'}.")
    if disclosure.statement(session) and not disclosure.is_confirmed(session):
        add("ch3.ethics", "Read and confirm your AI-use statement. It changes whenever AI is used again.")

    attach_issues(session, disabled, add)
    citation_issues(session, disabled, add)
    alignment_issues(session, add)

    all_text = " ".join(plain((session.draft(k) or {}).get("html", "")) for k in spec_module.report_keys())
    for match in sorted(set(re.findall(r"\{\{ref:appendix:([\w.]+)\}\}", all_text))):
        if f"app.{match}" in disabled:
            add("setup.options", f"Your text refers to an appendix you left out: {match}.")
    return issues


def attach_issues(session: Session, disabled: set[str], add) -> None:
    for part in spec_module.all_parts():
        config = part.get("attach")
        if not config or part["key"] in disabled:
            continue
        files = session.attachments(part["key"])
        if config.get("consent"):
            missing = [f for f in files if not f["consent"]]
            if missing:
                add(part["key"], f"{len(missing)} photos are left out until you confirm consent.", blocking=False)
        if config.get("captioned") and any(not f["caption"] for f in files):
            add(part["key"], "Write a caption for every file.")
        if config.get("caption_id") and not files:
            add(part["key"], f"Add the {config['label'].lower()}.")
        saved = session.diagram(part["key"])
        if saved and any(f["id"] == saved["rendered"] for f in files) and saved.get("drawn") != saved["spec"]:
            add(part["key"], "You changed this diagram's table after drawing it. Draw it again.")
        if config.get("caption_id") and len(files) > 1:
            add(part["key"], "Keep one file here. Only the first one is placed as a figure.", blocking=False)


def source_quality(sources: list[dict], add) -> None:
    """The template's standards for sources. These are prompts, because exceptions can be justified."""
    if sources and len(sources) < 15:
        add("ch2.sources", f"You have {len(sources)} sources. Aim for at least 15 credible sources.", blocking=False)
    cutoff = date.today().year - 10
    for source in sources:
        name = (source.get("title") or "")[:50]
        year = str(source.get("year") or "").strip()
        if year.isdigit() and int(year) < cutoff:
            add("ch2.sources", f"Older than 10 years ({year}): {name}. Use recent sources where you can.", blocking=False)
        if "wikipedia" in " ".join(str(source.get(k) or "") for k in ("container", "link", "title")).lower():
            add("ch2.sources", f"Wikipedia is not a credible research source: {name}.", blocking=False)
        if not (source.get("authors") or "").strip() or not year:
            add("ch2.sources", f"Missing author or year: {name}.", blocking=False)


COMMON_WORDS = {"and", "the", "for", "module", "management", "system"}


def _mentioned(phrase: str, text: str) -> bool:
    words = [w for w in re.findall(r"[a-z0-9]+", phrase.lower()) if len(w) > 2 and w not in COMMON_WORDS]
    lowered = text.lower()
    return all(w in lowered for w in words)


def alignment_issues(session: Session, add) -> None:
    """Scope, modules, and the framework should describe the same system. These are prompts to look."""
    modules = g.lines(g.inputs_for(session, "setup.system").get("modules"))
    for key, where in (("ch1.scope", "Scope"), ("ch4.design.modules", "Modules and UI")):
        draft = session.draft(key)
        if draft:
            missing = [m for m in modules if not _mentioned(m, plain(draft.get("html", "")))]
            if missing:
                add(key, f"{where} does not mention: {', '.join(missing)}. The panel checks your output against your modules.", blocking=False)
    ipo = (session.diagram("ch1.framework") or {}).get("spec")
    model = (session.diagram("ch3.method.overview") or {}).get("spec")
    if ipo and model:
        process = [n["label"] for n in ipo["nodes"] if n["type"] == "Process"]
        phases = [n["label"] for n in model["nodes"]]
        if len(process) != len(phases) or not all(_mentioned(a.split()[0], b) for a, b in zip(process, phases)):
            add("ch1.framework", "The Process items in your framework do not match the phases in your development model diagram.", blocking=False)


def citation_issues(session: Session, disabled: set[str], add) -> None:
    sources = g.sources_of(session)
    manual = g.inputs_for(session, "refs.list").get("entries") or ""
    source_quality(sources, add)
    texts = {
        key: plain((session.draft(key) or {}).get("html", ""))
        for key in spec_module.report_keys() if key not in disabled and key != "refs.list"
    }
    combined = " ".join(texts.values())
    for key, text in texts.items():
        for name, year in sorted(apa.find_citations(text)):
            if not apa.citation_exists(name, year, sources, manual):
                add(key, f"The citation {name}, {year} is not in your Source Library. Add the real source, remove the citation, or reword the sentence if it is not a citation.")
    for source in sources:
        if not apa.is_cited(source, combined):
            add("ch2.sources", f"Not cited in your paper: {apa.surname(source)} ({source.get('year') or 'n.d.'}). Cite it or remove it.", blocking=False)
