"""One AI action on one part: gather facts, mask them, call the relay, validate, restore names."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import re
import threading
import time
from contextlib import contextmanager
from html import escape
from typing import Literal

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, ValidationError

from app.diagrams import clean_spec
from app.parts import export as exporter
from app.parts import generators as g
from app.parts import spec as spec_module
from app.session import Session
from app.sources import apa

from .client import AIError, RelayClient
from .masking import Masker
from .validators import Facts, check_text

PROVIDER = "cloudflare-workers-ai"
MAX_REQUEST_CHARS = 34000
CONTEXT_CHARS = 2500
MAX_SOURCES = 30
NOTE_CHARS = 500
NEURONS_PER_TASK = 25  # measured 13 to 26 on the live relay; long Chapter 2 parts cost more
MIN_WORDS = 6
GUIDED_MINIMUM = 3
MAX_TOPICS = 6
SOURCE_QUESTION = re.compile(r"\b(?:sources?|studies|study|references?|citations?)\b", re.IGNORECASE)
STATUS_SECONDS = 30
MAX_IMAGE_OPTIONS = 4
MAX_IMAGE_DESCRIPTION = 300


class Block(BaseModel):
    type: Literal["paragraph", "heading3", "list"]
    text: str = ""
    lead: str = ""
    items: list[str] = Field(default_factory=list)


class WriteReply(BaseModel):
    blocks: list[Block] = Field(default_factory=list)
    citations_used: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)


class SuggestReply(BaseModel):
    suggestions: list[str] = Field(min_length=1, max_length=8)


class Finding(BaseModel):
    severity: Literal["fix", "consider"] = "consider"
    message: str


class CheckReply(BaseModel):
    findings: list[Finding] = Field(default_factory=list, max_length=15)


class DiagramReply(BaseModel):
    nodes: list[dict] = Field(default_factory=list, max_length=60)
    edges: list[dict] = Field(default_factory=list, max_length=100)
    missing_info: list[str] = Field(default_factory=list)


class Slide(BaseModel):
    title: str
    bullets: list[str] = Field(default_factory=list)
    notes: str = ""


class PanelQuestion(BaseModel):
    question: str
    answer_hint: str = ""


class DefenseReply(BaseModel):
    slides: list[Slide] = Field(min_length=5, max_length=18)
    questions: list[PanelQuestion] = Field(default_factory=list, max_length=20)


class AutoReply(BaseModel):
    blocks: list[Block] = Field(default_factory=list)
    citations_used: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)


class RowsReply(BaseModel):
    rows: list[dict] = Field(default_factory=list, max_length=80)


class Option(BaseModel):
    name: str
    why_tl: str = ""
    why_en: str = ""


class OptionsReply(BaseModel):
    options: list[Option] = Field(min_length=1, max_length=6)


class FeatureRow(BaseModel):
    feature: str
    a: str = "Not stated"
    b: str = "Not stated"
    c: str = "Not stated"
    own: str = "No"


class SynthesisReply(BaseModel):
    system_names: list[str] = Field(default_factory=list, max_length=3)
    features: list[FeatureRow] = Field(default_factory=list, max_length=15)
    blocks: list[Block] = Field(min_length=1)
    citations_used: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)


class Topic(BaseModel):
    topic: str
    search: str


class TopicsReply(BaseModel):
    topics: list[Topic] = Field(min_length=1, max_length=8)


class GuidedQuestion(BaseModel):
    section: str = ""
    question_en: str
    question_tl: str


class GuidedReply(BaseModel):
    blocks: list[Block] = Field(min_length=1)
    questions: list[GuidedQuestion] = Field(min_length=1, max_length=12)
    search_terms: list[str] = Field(default_factory=list, max_length=6)
    topics: list[str] = Field(default_factory=list, max_length=8)


SCHEMAS = {"rows": RowsReply, "auto": AutoReply, "options": OptionsReply, "synthesis": SynthesisReply, "topics": TopicsReply, "guided": GuidedReply, "write": WriteReply, "suggest": SuggestReply, "check": CheckReply, "diagram": DiagramReply, "defense": DefenseReply}
DEFENSE_RULES = {"V1", "V2", "V3"}  # voice, length, and tables apply to the paper, not to an outline


def parse_reply(text: str, schema: type[BaseModel]) -> BaseModel:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("The reply was not a JSON object.")
    try:
        return schema.model_validate(json.loads(text[start:end + 1]))
    except json.JSONDecodeError as exc:
        raise ValueError(f"The reply was not valid JSON: {exc.msg}.") from exc
    except ValidationError as exc:
        # Locations and messages only, so no student text is echoed back.
        details = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5])
        raise ValueError(f"The JSON did not match the schema. {details}") from exc


def cell_text(cell) -> str:
    return str(cell.get("text", "")) if isinstance(cell, dict) else str(cell)


def table_lines(block: dict) -> dict:
    rows = []
    for row in block.get("rows", []):
        if isinstance(row, dict) and "span" in row:
            rows.append([row["span"]])
        else:
            rows.append([cell_text(c) for c in (row["cells"] if isinstance(row, dict) else row)])
    item = {"title": block.get("title", ""), "columns": [c["label"] for c in block.get("columns", [])], "rows": rows}
    if block.get("caption_id"):
        item["ref"] = f"{{{{ref:table:{block['caption_id']}}}}}"
    return item


def blocks_text(blocks: list[dict]) -> str:
    out: list[str] = []
    for block in blocks:
        if block.get("type") == "table":
            data = table_lines(block)
            out.append(" | ".join([data["title"], *data["columns"]]))
            out.extend(" | ".join(r) for r in data["rows"])
        elif block.get("type") == "list":
            for item in block["items"]:
                out.append(item["text"] if isinstance(item, dict) else item)
                out.extend(item.get("children", []) if isinstance(item, dict) else [])
        elif block.get("html") or block.get("text"):
            out.append(exporter.plain(block.get("html") or block.get("text")))
    return "\n".join(out)


def draft_text(draft: dict | None) -> str:
    if not draft:
        return ""
    # Tables come first so that a long discussion cannot push results past the context limit.
    return "\n".join(t for t in (blocks_text(draft.get("before", [])), blocks_text(draft.get("after", [])), exporter.plain(draft.get("html", ""))) if t)


def cite_as(source: dict) -> str:
    authors = apa.split_authors(source.get("authors"))
    surnames = [a.split(",")[0].strip() for a in authors]
    year = str(source.get("year") or "n.d.").strip()
    if not surnames:
        return f"{apa.surname(source)}, {year}"
    if len(surnames) == 1:
        return f"{surnames[0]}, {year}"
    if len(surnames) == 2:
        return f"{surnames[0]} & {surnames[1]}, {year}"
    return f"{surnames[0]} et al., {year}"


def blocks_to_html(blocks: list[Block], restore) -> str:
    html = ""
    for block in blocks:
        text = escape(restore(block.text).strip(), quote=False)
        if block.type == "heading3" and text:
            html += f"<h3>{text}</h3>"
        elif block.type == "list" and block.items:
            html += "<ol>" + "".join(f"<li>{escape(restore(i).strip(), quote=False)}</li>" for i in block.items if i.strip()) + "</ol>"
        elif block.type == "paragraph" and text:
            lead = escape(restore(block.lead).strip().rstrip("."), quote=False)
            html += f"<p><strong>{lead}.</strong> {text}</p>" if lead else f"<p>{text}</p>"
    return html


class AIService:
    def __init__(self, settings):
        self.client = RelayClient(settings)
        self.running = threading.Lock()
        self.last_ready: tuple[float, dict] | None = None
        self.update_url = ""

    # ---- what the page may offer ----------------------------------------------------

    def status(self) -> dict:
        if not self.client.configured:
            return {"state": "off"}
        # Students move between pages constantly. A recent good answer keeps the buttons responsive.
        if self.last_ready and time.monotonic() - self.last_ready[0] < STATUS_SECONDS:
            return self.last_ready[1]
        self.last_ready = None
        try:
            health = self.client.health()
            quota = self.client.quota()
        except AIError as exc:
            return {"state": "unavailable", "message": str(exc)}
        tasks = min(quota["install_remaining"], quota["overall_remaining"]) // NEURONS_PER_TASK
        self.update_url = self.client.update_available(health)
        answer = {"state": "ready", "tasks_left": tasks, "update": bool(self.update_url)}
        self.last_ready = (time.monotonic(), answer)
        return answer

    @staticmethod
    def setup_gaps(session: Session, key: str) -> list[str]:
        """Project Setup answers that a first draft cannot be written without."""
        needed = ((spec_module.ai_config(key) or {}).get("guided") or {}).get("setup", [])
        values = g.inputs_for(session, "setup.system")
        labels = {f["id"]: f["label"] for f in spec_module.part("setup.system")["inputs"]}
        return [labels[name] for name in needed if not str(values.get(name) or "").strip()]

    @staticmethod
    def blocked(session: Session, key: str) -> list[str]:
        """Required real inputs that are still missing. AI is disabled until these exist."""
        config = spec_module.ai_config(key) or {}
        reasons: list[str] = []
        if not (g.inputs_for(session, "setup.system").get("system_name") or "").strip():
            reasons.append("Enter your system name in Project Setup, System Profile.")
        part = spec_module.part(key)
        values = g.inputs_for(session, key)
        fields = {f["id"]: f for f in part["inputs"]}
        for name in config.get("requires", []):
            field = fields[name]
            value = values.get(name)
            if field["type"] in ("grid", "repeater"):
                editable = [c["id"] for c in field.get("columns", field.get("fields", [])) if not c.get("readonly") and c.get("type") != "hidden"]
                if not any(str(row.get(c) or "").strip() for row in value or [] for c in editable):
                    reasons.append(f"Fill in: {field['label']}.")
            elif not str(value or "").strip():
                reasons.append(f"Fill in: {field['label']}.")
            elif field["type"] == "textarea" and len(str(value).split()) < MIN_WORDS:
                reasons.append(f"Add more detail to: {field['label']}.")
        if config.get("guided"):
            answers = values.get("answers") or []
            answered = sum(1 for row in answers if (row.get("answer") or "").strip())
            needed = min(len(answers), max(GUIDED_MINIMUM, (len(answers) + 1) // 2))
            typed_notes = any(str(values.get(name) or "").strip() for name in config.get("requires", []))
            if answered >= needed and answers:
                reasons = [r for r in reasons if "System Profile" in r]
            elif answers:
                reasons = [f"Sagutin ang kahit {needed} tanong sa Hakbang 2. (Answer at least {needed} questions in Step 2.)"]
            elif not typed_notes:
                reasons = ["Gawin muna ang unang draft sa Hakbang 1. (Make the first draft in Step 1.)"]
        if config.get("auto_research") or config.get("auto_draft"):
            reasons = [r for r in reasons if "System Profile" in r]
        auto_settings = spec_module.auto_config(key)
        auto = auto_settings
        if auto is not None:
            for item in auto.get("wait_for", []):
                name, _, column = item.partition(".")
                field, value = fields[name], values.get(name)
                if column:
                    present = any(str(row.get(column) or "").strip() for row in value or [])
                elif field["type"] == "grid":
                    editable = [c["id"] for c in field["columns"] if not c.get("readonly")]
                    present = any(str(row.get(c) or "").strip() for row in value or [] for c in editable)
                else:
                    present = bool(str(value or "").strip())
                if not present:
                    reasons.append(f"Fill in: {field['label']}.")
            typed = any(str(values.get(name) or "").strip() for name in config.get("requires", []))
            if auto.get("repo") and not session.get_meta("repo_digest") and not typed:
                reasons.append("Ikonekta muna ang code ng inyong sistema sa itaas. (Connect your system's code above first.)")
        for other in config.get("requires_parts", []):
            if other not in g.disabled_parts(session) and not session.draft(other):
                reasons.append(f"Build this part first: {spec_module.part(other)['label']}.")
        return reasons

    @staticmethod
    def _auto_page(key: str) -> dict:
        auto = spec_module.auto_config(key) or {}
        return {
            "repo": bool(auto.get("repo")), "choices": auto.get("choices", []), "suggest_choices": bool(auto.get("suggest_choices")),
            "show": auto.get("show", []), "table_first": bool(auto.get("table_first")), "waits": bool(auto.get("wait_for")),
        }

    def page_info(self, session: Session, key: str) -> dict:
        config = spec_module.ai_config(key)
        if not config or not self.client.configured:
            return {"configured": False, "write": False, "check": False, "suggest": {}, "blocked": [], "repo": False, "choices": [], "suggest_choices": False, "show": [], "table_first": False, "waits": False, "auto_draft": False, "auto_research": False, "stepped": False, "research": False, "guided": False, "setup_gaps": [], "defense": False, "diagram": False, "images": None, "options": []}
        modes = config.get("modes", [])
        slot = config.get("images")
        return {
            "configured": True, "write": "write" in modes, "check": "check" in modes,
            "suggest": config.get("suggest", {}), "blocked": self.blocked(session, key) if {"write", "defense"} & set(modes) else [],
            "auto_research": bool(config.get("auto_research")),
            "auto_draft": bool(config.get("auto_draft")), **self._auto_page(key),
            "stepped": bool(config.get("auto_research") or config.get("auto_draft")) or "guided" in modes,
            "defense": "defense" in modes,
            "guided": "guided" in modes,
            "research": bool((config.get("guided") or {}).get("research")),
            "setup_gaps": self.setup_gaps(session, key) if "guided" in modes else [],
            "diagram": bool(((spec_module.part(key) or {}).get("attach") or {}).get("diagram")),
            "images": slot, "options": session.attachments(key, options=True) if slot else [],
        }

    # ---- what is sent ---------------------------------------------------------------

    @staticmethod
    def masker(session: Session) -> Masker:
        masker = Masker(keep_surnames={apa.surname(s) for s in g.sources_of(session)})
        school = g.inputs_for(session, "setup.school")
        people = g.inputs_for(session, "setup.people")
        masker.add(school.get("adviser"), "ADVISER")
        masker.add(school.get("principal"), "PRINCIPAL")
        for index, row in enumerate(people.get("proponents", []), start=1):
            masker.add(row.get("name"), f"PROPONENT_{index}")
        for index, name in enumerate(("panel_chair", "panel_member_1", "panel_member_2"), start=1):
            masker.add(people.get(name), f"PANEL_{index}")
        for index, row in enumerate(g.inputs_for(session, "prelim.acknowledgment").get("thanks", []), start=1):
            masker.add(row.get("who"), f"THANKED_{index}", variants=False)
        for index, row in enumerate(g.inputs_for(session, "app.revision").get("rows", []), start=1):
            masker.add(row.get("member"), f"REVIEWER_{index}", variants=False)
        return masker

    @staticmethod
    def _facts(session: Session) -> dict:
        system = g.inputs_for(session, "setup.system")
        names = ("system_name", "platform", "system_type", "users", "locale", "process", "dev_model", "modules", "roles")
        facts = {name: system.get(name, "") for name in names}
        school = g.inputs_for(session, "setup.school")
        facts["school"] = school.get("school_name", "")
        facts["research_theme"] = school.get("research_theme", "")
        return facts

    @staticmethod
    def _stage(session: Session) -> str:
        return "proposal" if g.inputs_for(session, "setup.options").get("stage") == "Proposal" else "final"

    @staticmethod
    def _inputs(session: Session, key: str) -> dict:
        part = spec_module.part(key)
        values = g.inputs_for(session, key)
        output: dict = {}
        for field in part["inputs"]:
            if field["type"] == "hidden":
                continue
            value = values.get(field["id"])
            if isinstance(value, list):
                value = [{k: v for k, v in row.items() if k != "rq_id"} if isinstance(row, dict) else row for row in value]
            output[field["id"]] = value
        return output

    def _sources(self, session: Session, text: str, key_filter: str = "") -> list[dict]:
        sources = g.sources_of(session)
        if key_filter:
            sources = [s for s in sources if key_filter in (s.get("used_for") or "").split(",")]
        # Sources the student already cited go first, so they survive the size limit.
        sources.sort(key=lambda s: not apa.is_cited(s, text))
        return [{
            "cite_as": cite_as(s), "title": s.get("title", ""), "origin": s.get("origin", ""),
            "notes": (s.get("notes") or "")[:NOTE_CHARS],
        } for s in sources[:MAX_SOURCES]]

    def build_payload(self, session: Session, key: str, mode: str, field: str | None = None) -> tuple[dict, dict]:
        """Return the unmasked payload and the offline draft that supplies tables."""
        config = spec_module.ai_config(key) or {}
        offline = g.generate(session, key)
        payload: dict = {"facts": self._facts(session), "inputs": self._inputs(session, key)}
        if config.get("tables"):
            tables = [b for b in [*offline.get("before", []), *offline.get("after", [])] if b.get("type") == "table"]
            payload["tables"] = [table_lines(t) for t in tables]
            notes = [b.get("text") for b in [*offline.get("before", []), *offline.get("after", [])] if b.get("type") in ("note", "formula") and b.get("text")]
            if notes:
                payload["computed_notes"] = notes
            if key == "ch4.testing":
                payload["computed_summary"] = exporter.plain(offline.get("html", "")).split("%.")[0] + "%."
        if config.get("sources"):
            payload["sources"] = self._sources(session, json.dumps(payload["inputs"], ensure_ascii=False), key if config.get("auto_research") else "")
        context = {spec_module.part(k)["label"]: draft_text(session.draft(k))[:CONTEXT_CHARS] for k in config.get("context", [])}
        context = {label: text for label, text in context.items() if text}
        if context:
            payload["context"] = context
        if config.get("people"):
            payload["people_roles"] = "{{ADVISER}} is the research adviser, {{PRINCIPAL}} is the school head, {{PANEL_1}} to {{PANEL_3}} are the panel, and {{THANKED_n}} are the people and offices listed in inputs."
        auto = spec_module.auto_config(key)
        if auto is not None:
            values = g.inputs_for(session, key)
            if values.get("choice"):
                payload["choice"] = values["choice"]
            if auto.get("repo") and session.get_meta("repo_digest"):
                payload["repository"] = json.loads(session.get_meta("repo_digest"))["text"]
        if mode == "topics":
            payload["inputs"] = {}
        if mode == "defense":
            payload["inputs"] = {}  # what the students typed here is theirs, and the report is the source
        if mode == "suggest":
            payload["field"] = field
        if mode == "check":
            payload["draft"] = draft_text(session.draft(key))
        return payload, offline

    @staticmethod
    def _shrink(masked: dict) -> dict:
        def size() -> int:
            return len(json.dumps(masked, ensure_ascii=False))

        while size() > MAX_REQUEST_CHARS and masked.get("sources"):
            masked["sources"].pop()
        if size() > MAX_REQUEST_CHARS and masked.get("context"):
            masked["context"] = {k: v[:800] for k, v in masked["context"].items()}
        if size() > MAX_REQUEST_CHARS:
            raise AIError("This part is too long for one AI request. Shorten your answers and try again.")
        return masked

    def facts_for_validation(self, session: Session, key: str, payload: dict, offline: dict) -> Facts:
        config = spec_module.ai_config(key) or {}
        tense = spec_module.ai_tense(key)
        if tense == "stage":
            tense = "future" if self._stage(session) == "proposal" else "past"
        captions = [b["caption_id"] for b in [*offline.get("before", []), *offline.get("after", [])] if b.get("type") == "table" and b.get("caption_id")]
        valid = {f"{{{{ref:appendix:{p['key'].split('.', 1)[1]}}}}}" for p in spec_module.all_parts() if p["key"].startswith("app.")}
        for other in spec_module.report_keys():
            draft = session.draft(other) or g.generate(session, other) if other == key else session.draft(other)
            for block in [*(draft or {}).get("before", []), *(draft or {}).get("after", [])]:
                if block.get("caption_id"):
                    valid.add(f"{{{{ref:{block['type']}:{block['caption_id']}}}}}")
            attach = (spec_module.part(other) or {}).get("attach") or {}
            if attach.get("caption_id") and session.attachments(other):
                valid.add(f"{{{{ref:figure:{attach['caption_id']}}}}}")
            elif attach.get("captioned"):
                short = re.sub(r"\W", "_", other)
                valid |= {f"{{{{ref:figure:{short}_{item['id']}}}}}" for item in session.attachments(other)}
        return Facts(
            payload_text=json.dumps(payload, ensure_ascii=False), sources=g.sources_of(session),
            manual_refs=g.inputs_for(session, "refs.list").get("entries") or "", tense=tense, words=config.get("words"),
            tables=[f"{{{{ref:table:{c}}}}}" for c in captions], valid_refs=valid, off_appendices=g.disabled_parts(session),
        )

    # ---- calling the relay ----------------------------------------------------------

    def _call(self, session: Session, key: str, mode: str, masked: dict, *, use_cache: bool) -> tuple[BaseModel, dict]:
        schema = SCHEMAS[mode]
        version = self.client.health()["prompt_version"]
        stage = self._stage(session)
        digest = hashlib.sha256(json.dumps([version, key, mode, stage, masked], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        cached = session.ai_cache_get(digest) if use_cache else None
        if cached:
            return parse_reply(cached["text"], schema), {"event": cached["event"], "cached": True}
        request = {"part_key": key, "mode": mode, "stage": stage, "masked_input": masked}
        reply = self.client.text(request)
        try:
            parsed = parse_reply(reply["text"], schema)
        except ValueError as first:
            reply = self.client.text({**request, "fix_hint": str(first)})
            try:
                parsed = parse_reply(reply["text"], schema)
            except ValueError as second:
                session.log_ai(key, mode, PROVIDER, reply.get("model", ""), reply.get("prompt_version", ""), reply.get("usage"), "unusable")
                raise AIError("The AI reply could not be used. Your text was not changed. Try again.") from second
        event = session.log_ai(key, mode, PROVIDER, reply.get("model", ""), reply.get("prompt_version", ""), reply.get("usage"), "generated")
        session.ai_cache_put(digest, reply["text"], event)
        return parsed, {"event": event, "cached": False}

    @contextmanager
    def _exclusive(self):
        if not self.running.acquire(blocking=False):
            raise AIError("Another AI task is still running. Wait for it to finish.")
        try:
            yield
        finally:
            self.last_ready = None  # the remaining allowance has changed
            self.running.release()

    def write(self, session: Session, key: str) -> dict:
        config = spec_module.ai_config(key) or {}
        if "write" not in config.get("modes", []):
            raise AIError("AI drafting is not available for this part.")
        reasons = self.blocked(session, key)
        if reasons:
            raise AIError("Before AI can draft this part: " + " ".join(reasons))
        with self._exclusive():
            payload, offline = self.build_payload(session, key, "write")
            masker = self.masker(session)
            masked = self._shrink(masker.mask_tree(payload))
            previous = (session.draft(key) or {}).get("ai") or {}
            # Asking again for the same inputs means the student wants a different version.
            reply, meta = self._call(session, key, "write", masked, use_cache=previous.get("last_masked") != hash_of(masked))
            if not reply.blocks:
                needs = " ".join(masker.restore(m) for m in reply.missing_info) or "Add more detail to your answers."
                raise AIError(f"AI did not write a draft because information is missing. {needs}")
            html = blocks_to_html(reply.blocks, masker.restore)
            if key == "prelim.abstract" and (g.inputs_for(session, key).get("keywords") or "").strip():
                html += f"<p><strong>Keywords:</strong> {escape(g.inputs_for(session, key)['keywords'].strip(), quote=False)}</p>"
            facts = self.facts_for_validation(session, key, payload, offline)
            used = [masker.restore(c) for c in reply.citations_used]
            issues = check_text(exporter.plain(html).split("Keywords:")[0], facts, used)
            draft = {**offline, "html": html, "ai": {
                "event": meta["event"], "last_masked": hash_of(masked), "issues": issues,
                "missing_info": [masker.restore(m) for m in reply.missing_info], "hidden_contacts": masker.contacts_found,
            }}
            session.save_draft(key, draft, "ai")
            return {"issues": len(issues), "cached": meta["cached"]}

    def guided(self, session: Session, key: str) -> dict:
        """Write a first draft from Project Setup, with gaps marked, and ask for what is missing."""
        if "guided" not in (spec_module.ai_config(key) or {}).get("modes", []):
            raise AIError("This is not available here.")
        gaps = self.setup_gaps(session, key)
        if gaps:
            raise AIError("Kumpletuhin muna ang Project Setup, System Profile. (Complete System Profile first.) " + " ".join(f"{g_}." for g_ in gaps))
        with self._exclusive():
            payload, offline = self.build_payload(session, key, "guided")
            masker = self.masker(session)
            masked = self._shrink(masker.mask_tree(payload))
            reply, meta = self._call(session, key, "guided", masked, use_cache=False)
            html = blocks_to_html(reply.blocks, masker.restore)
            values = g.inputs_for(session, key)
            earlier = {(row.get("question_en") or "").strip().lower(): row.get("answer", "") for row in values.get("answers") or []}
            keep = [row for row in values.get("answers") or [] if (row.get("answer") or "").strip()]
            asked = {(row.get("question_en") or "").strip().lower() for row in keep}
            for question in reply.questions:
                english = masker.restore(question.question_en).strip()
                if SOURCE_QUESTION.search(english):  # the app finds sources itself, so students are not asked
                    continue
                if english.lower() not in asked:
                    keep.append({"section": masker.restore(question.section), "question_en": english, "question_tl": masker.restore(question.question_tl).strip(), "answer": earlier.get(english.lower(), "")})
            values["answers"] = keep
            if (spec_module.ai_config(key).get("guided") or {}).get("topics") and reply.topics:
                reviews = {(row.get("topic") or "").strip().lower(): row.get("review", "") for row in values.get("topics") or []}
                values["topics"] = [{"topic": t.strip(), "review": reviews.get(t.strip().lower(), "")} for t in reply.topics if t.strip()]
            values["search_terms"] = "\n".join(t.strip() for t in reply.search_terms if t.strip())
            session.save_inputs(key, values)
            facts = self.facts_for_validation(session, key, payload, offline)
            issues = [i for i in check_text(exporter.plain(html), facts) if i["rule"] != "V3"]  # marked gaps are expected in a first draft
            session.save_draft(key, {**offline, "html": html, "ai": {"event": meta["event"], "issues": issues, "missing_info": [], "hidden_contacts": masker.contacts_found, "first_draft": True}}, "ai")
            return {"questions": len(reply.questions)}

    def recommend_options(self, session: Session, key: str) -> dict:
        auto = spec_module.auto_config(key) or {}
        if not auto.get("suggest_choices"):
            raise AIError("This is not available here.")
        with self._exclusive():
            payload, _ = self.build_payload(session, key, "options")
            masker = self.masker(session)
            reply, _meta = self._call(session, key, "options", self._shrink(masker.mask_tree(payload)), use_cache=False)
            values = g.inputs_for(session, key)
            values["options_json"] = json.dumps([{"name": masker.restore(o.name), "why_tl": masker.restore(o.why_tl), "why_en": masker.restore(o.why_en)} for o in reply.options], ensure_ascii=False)
            session.save_inputs(key, values)
            return {"options": len(reply.options)}

    def auto_write(self, session: Session, key: str, rows_only: bool = False) -> dict:
        """Write a part from Project Setup and the approved parts. Students then edit and approve it."""
        auto = spec_module.auto_config(key)
        values = g.inputs_for(session, key)
        if (auto.get("choices") or auto.get("suggest_choices")) and not values.get("choice"):
            raise AIError("Pumili muna ng isa sa mga pagpipilian. (Choose one of the options first.)")
        reasons = [] if rows_only else self.blocked(session, key)
        if reasons:
            raise AIError(" ".join(reasons))
        with self._exclusive():
            payload, offline = self.build_payload(session, key, "auto")
            masker = self.masker(session)
            fill = auto.get("fill")
            if fill and not any(any(str(row.get(c) or "").strip() for c in fill["columns"]) for row in g.inputs_for(session, key).get(fill["field"]) or []):
                # The table is its own request: asked together, the model writes the prose and skips it.
                masked_rows = self._shrink(masker.mask_tree({**payload, "row_keys": fill["columns"]}))
                table, _ = self._call(session, key, "rows", masked_rows, use_cache=False)
                if not table.rows:  # the model occasionally answers with an empty list
                    table, _ = self._call(session, key, "rows", masked_rows, use_cache=False)
                values = g.inputs_for(session, key)
                values[fill["field"]] = [{c: masker.restore(str(row.get(c) or "")).strip() for c in fill["columns"]} for row in table.rows if any(row.get(c) for c in fill["columns"])]
                session.save_inputs(key, values)
                payload, offline = self.build_payload(session, key, "auto")
            if rows_only:
                count = len(g.inputs_for(session, key).get(fill["field"]) or [])
                if not count:
                    raise AIError("Hindi nakagawa ng talahanayan. Subukan muli. (The table could not be prepared. Try again.)")
                return {"rows": count}
            reply, meta = self._call(session, key, "auto", self._shrink(masker.mask_tree(payload)), use_cache=False)
            if not reply.blocks:
                needs = " ".join(masker.restore(m) for m in reply.missing_info) or "Try again."
                raise AIError(f"AI did not write a draft. {needs}")
            html = blocks_to_html(reply.blocks, masker.restore)
            facts = self.facts_for_validation(session, key, payload, offline)
            issues = [i for i in check_text(exporter.plain(html), facts, [masker.restore(c) for c in reply.citations_used]) if i["rule"] != "V3"]
            session.save_draft(key, {**offline, "html": html, "ai": {"event": meta["event"], "issues": issues, "missing_info": [masker.restore(m) for m in reply.missing_info], "hidden_contacts": masker.contacts_found}}, "ai")
            return {"issues": len(issues)}

    def synthesize(self, session: Session, key: str, rows_only: bool = False) -> dict:
        """Write the part from the approved parts before it, with a first feature comparison."""
        auto = spec_module.auto_config(key)
        if auto is None:
            raise AIError("This is not available here.")
        if auto.get("mode") != "synthesis":
            return self.auto_write(session, key, rows_only)
        reasons = self.blocked(session, key)
        if reasons:
            raise AIError(" ".join(reasons))
        with self._exclusive():
            payload, _ = self.build_payload(session, key, "synthesis")
            masker = self.masker(session)
            reply, meta = self._call(session, key, "synthesis", self._shrink(masker.mask_tree(payload)), use_cache=False)
            restore = masker.restore

            def cell(value: str) -> str:
                return "Yes" if value.strip().lower() == "yes" else "Not stated"

            values = g.inputs_for(session, key)
            names = [restore(n).strip() for n in reply.system_names] + ["", "", ""]
            values.update(system_a=names[0], system_b=names[1], system_c=names[2])
            values["matrix"] = [{"feature": restore(r.feature).strip(), "a": cell(r.a), "b": cell(r.b), "c": cell(r.c), "own": "Yes" if r.own.strip().lower() == "yes" else "No"} for r in reply.features]
            session.save_inputs(key, values)
            offline = g.generate(session, key)
            html = blocks_to_html(reply.blocks, restore)
            issues = check_text(exporter.plain(html), self.facts_for_validation(session, key, payload, offline), [restore(c) for c in reply.citations_used])
            draft = {**offline, "html": html, "ai": {"event": meta["event"], "issues": issues, "missing_info": [restore(m) for m in reply.missing_info], "hidden_contacts": masker.contacts_found}}
            session.save_draft(key, draft, "ai")
            return {"issues": len(issues)}

    def recommend_topics(self, session: Session, key: str) -> dict:
        """Recommend what to research, from the Chapter 1 the students approved. They edit and approve it."""
        if not (spec_module.ai_config(key) or {}).get("auto_research"):
            raise AIError("This is not available here.")
        reasons = self.blocked(session, key)
        if reasons:
            raise AIError(" ".join(reasons))
        with self._exclusive():
            payload, _ = self.build_payload(session, key, "topics")
            masker = self.masker(session)
            reply, _meta = self._call(session, key, "topics", self._shrink(masker.mask_tree(payload)), use_cache=False)
            values = g.inputs_for(session, key)
            values["research_topics"] = [{"topic": masker.restore(t.topic).strip(), "search": masker.restore(t.search).strip()} for t in reply.topics]
            session.save_inputs(key, values)
            return {"topics": len(reply.topics)}

    def research(self, session: Session, key: str) -> dict:
        """After the students approve the topics: find real studies, add them, and write the draft."""
        config = (spec_module.ai_config(key) or {}).get("auto_research")
        if not config:
            raise AIError("This is not available here.")
        rows = [r for r in g.inputs_for(session, key).get("research_topics") or [] if (r.get("topic") or "").strip()][:MAX_TOPICS]
        if not rows:
            raise AIError("Maglagay muna ng kahit isang paksa. (Add at least one topic first.)")
        found: dict[str, dict] = {}
        try:
            for row in rows:
                term = (row.get("search") or row["topic"]).strip()
                searches = [term, f"{term} Philippines"] if config.get("local_search") else [term]
                for query in searches:
                    usable = [s for s in apa.search_studies(query) if s["notes"]]
                    for study in usable[:config["per_topic"]]:
                        found.setdefault(study["title"].strip().lower(), study)
        except apa.LookupError_ as exc:
            raise AIError("Hindi makakonekta sa database ng mga pag-aaral. Subukan muli. (The scholarly database could not be reached. Try again.)") from exc
        if len(found) < config["minimum_sources"]:
            raise AIError(f"{len(found)} na pag-aaral lang ang nakita. Palitan ang mga salitang hahanapin. (Only {len(found)} studies were found. Change the search words.)")
        self._add_sources(session, key, list(found.values()))
        return {**self.write(session, key), "sources": len(found)}

    @staticmethod
    def _add_sources(session: Session, key: str, studies: list[dict]) -> None:
        values = g.inputs_for(session, "ch2.sources")
        library = values.setdefault("sources", [])
        by_title = {(s.get("title") or "").strip().lower(): s for s in library}
        for study in studies:
            existing = by_title.get(study["title"].strip().lower())
            if existing is None:
                existing = {**study, "used_for": ""}
                library.append(existing)
                by_title[study["title"].strip().lower()] = existing
            used = {u for u in (existing.get("used_for") or "").split(",") if u}
            existing["used_for"] = ",".join(sorted(used | {key}))
        session.save_inputs("ch2.sources", values)

    def propose_diagram(self, session: Session, key: str, kind: str) -> dict:
        """Ask for a first version of a diagram. The student edits it before anything is drawn."""
        kinds = ((spec_module.part(key) or {}).get("attach") or {}).get("diagram") or []
        if kind not in kinds:
            raise AIError("AI diagrams are not available for this part.")
        reasons = self.blocked(session, key)
        if reasons:
            raise AIError("Before AI can propose this diagram: " + " ".join(reasons))
        with self._exclusive():
            payload, _ = self.build_payload(session, key, "diagram")
            payload["kind"] = kind
            masker = self.masker(session)
            masked = self._shrink(masker.mask_tree(payload))
            reply, meta = self._call(session, key, "diagram", masked, use_cache=False)

            def restored(items: list[dict], names: tuple[str, ...]) -> list[dict]:
                return [{k: masker.restore(str(v)) if k in names and v is not None else v for k, v in item.items()} for item in items if isinstance(item, dict)]

            spec = clean_spec(kind, {"nodes": restored(reply.nodes, ("label", "details")), "edges": restored(reply.edges, ("label",))})
            if not spec["nodes"]:
                needs = " ".join(masker.restore(m) for m in reply.missing_info) or "Add more detail to your answers."
                raise AIError(f"AI did not propose a diagram because information is missing. {needs}")
            return {"spec": spec, "event": meta["event"], "missing_info": [masker.restore(m) for m in reply.missing_info]}

    def create_image(self, session: Session, key: str, description: str) -> dict:
        """Create one option for an allowed slot. It stays out of the report until the student keeps it."""
        slot = (spec_module.ai_config(key) or {}).get("images")
        if not slot:
            raise AIError("AI images are not available here. Use your real files for evidence.")
        description = " ".join(description.split())[:MAX_IMAGE_DESCRIPTION]
        if len(description.split()) < 3:
            raise AIError("Describe the image in at least a few words.")
        with self._exclusive():
            prompt = self.masker(session).mask(description)
            reply = self.client.image({"slot": slot, "prompt": prompt})
            try:
                with Image.open(io.BytesIO(base64.b64decode(reply["image_base64"], validate=True))) as picture:
                    out = io.BytesIO()
                    picture.convert("RGB").save(out, format="PNG", optimize=True)
            except (KeyError, binascii.Error, OSError, UnidentifiedImageError, ValueError) as exc:
                raise AIError("The image could not be read. Try again.") from exc
            usage = {**(reply.get("usage") or {}), "prompt": prompt, "slot": slot}
            event = session.log_ai(key, "image", PROVIDER, reply.get("model", ""), reply.get("prompt_version", ""), usage, "generated")
            attachment = session.add_attachment(key, "ai_option", f"AI {slot} {event}.png", "image/png", out.getvalue(), description, ai=True)
            for old in session.attachments(key, options=True)[:-MAX_IMAGE_OPTIONS]:
                session.delete_attachment(old["id"])
            return {"attachment": attachment, "event": event}

    def defense(self, session: Session, key: str) -> dict:
        if "defense" not in (spec_module.ai_config(key) or {}).get("modes", []):
            raise AIError("This is not available here.")
        reasons = self.blocked(session, key)
        if reasons:
            raise AIError("Before AI can prepare this: " + " ".join(reasons))
        with self._exclusive():
            payload, _ = self.build_payload(session, key, "defense")
            masker = self.masker(session)
            masked = self._shrink(masker.mask_tree(payload))
            reply, _meta = self._call(session, key, "defense", masked, use_cache=False)
            restore = masker.restore
            slides = [{"title": restore(s.title), "bullets": "\n".join(restore(b) for b in s.bullets), "notes": restore(s.notes)} for s in reply.slides[:15]]
            questions = [{"question": restore(q.question), "answer": ""} for q in reply.questions]
            hints = [restore(q.answer_hint) for q in reply.questions]
            for row, hint in zip(questions, hints):
                row["answer"] = f"Hint: {hint}" if hint else ""
            text = " ".join([*(f"{s['title']} {s['bullets']} {s['notes']}" for s in slides), *(q["question"] for q in questions), *hints])
            facts = Facts(payload_text=json.dumps(payload, ensure_ascii=False), sources=g.sources_of(session), manual_refs=g.inputs_for(session, "refs.list").get("entries") or "")
            issues = [i for i in check_text(text, facts) if i["rule"] in DEFENSE_RULES]
            session.save_inputs(key, {"slides": slides, "questions": questions})
            session.set_meta("defense_issues", json.dumps(issues, ensure_ascii=False))
            return {"issues": len(issues)}

    def suggest(self, session: Session, key: str, field: str) -> dict:
        config = spec_module.ai_config(key) or {}
        setting = config.get("suggest", {}).get(field)
        if not setting:
            raise AIError("AI suggestions are not available for this field.")
        if not (g.inputs_for(session, "setup.system").get("system_name") or "").strip():
            raise AIError("Enter your system name in Project Setup, System Profile, first.")
        with self._exclusive():
            payload, _ = self.build_payload(session, key, "suggest", field)
            masker = self.masker(session)
            masked = self._shrink(masker.mask_tree(payload))
            reply, _meta = self._call(session, key, "suggest", masked, use_cache=False)
            sources = g.sources_of(session)
            kept = []
            for text in reply.suggestions:
                restored = masker.restore(text).strip()
                if restored and all(apa.citation_exists(n, y, sources, "") for n, y in apa.find_citations(restored)):
                    kept.append(restored)
            return {"suggestions": kept, "target": setting.get("target")}

    def check(self, session: Session, key: str) -> dict:
        config = spec_module.ai_config(key) or {}
        if "check" not in config.get("modes", []):
            raise AIError("AI checking is not available for this part.")
        if not draft_text(session.draft(key)).strip():
            raise AIError("Build this part first, then check it.")
        with self._exclusive():
            payload, offline = self.build_payload(session, key, "check")
            masker = self.masker(session)
            masked = self._shrink(masker.mask_tree(payload))
            reply, _meta = self._call(session, key, "check", masked, use_cache=True)
            findings = [{"severity": f.severity, "message": masker.restore(f.message)} for f in reply.findings]
            return {"findings": findings, "rules": self.rule_issues(session, key, offline=offline)}

    def rule_issues(self, session: Session, key: str, offline: dict | None = None) -> list[dict]:
        """Re-run the rule checks on the text as it stands now."""
        payload, built = self.build_payload(session, key, "write")
        facts = self.facts_for_validation(session, key, payload, offline or built)
        text = exporter.plain((session.draft(key) or {}).get("html", "")).split("Keywords:")[0]
        return check_text(text, facts) if text else []


def hash_of(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
