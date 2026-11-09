"""The AI-use statement for Chapter 3, written only from what this session's log recorded."""
from __future__ import annotations

import hashlib
import json

from app.session import Session

from . import generators as g
from . import spec as spec_module

MODEL_NAMES = {
    "@cf/google/gemma-4-26b-a4b-it": "Google Gemma 4",
    "@cf/black-forest-labs/flux-1-schnell": "FLUX.1 schnell",
    "@cf/mistralai/mistral-small-3.1-24b-instruct": "Mistral Small 3.1",
}
PROVIDER_NAME = "Cloudflare Workers AI"
META_KEY = "disclosure_confirmed"
REPORT_KINDS = {"write", "check", "suggest", "diagram"}  # Defense Prep and logos are not part of the paper
OUTSIDE_THE_PAPER = {"setup.system", "finish.defense"}
DEVELOPMENT_SENTENCE = "The use of generative artificial intelligence in preparing this report is disclosed under Ethical Considerations."
RESPONSIBILITY = (
    "The tool received the researchers' own notes with personal names and contact details replaced. "
    "It was not used to produce data, statistical results, screenshots, photographs, or signatures, and all statistical results were computed from the actual responses. "
    "The researchers reviewed the AI-assisted material and remain responsible for the accuracy and integrity of this report."
)


def _events(session: Session) -> list[dict]:
    return [e for e in session.ai_events() if e["outcome"] != "unusable"]


def _model(identifier: str) -> str:
    return MODEL_NAMES.get(identifier, identifier)


def _grouped(keys: set[str]) -> str:
    """Name the parts in report order, grouped by tab: Chapter 1 (Background and Scope)."""
    groups = []
    for tab in spec_module.tabs():
        labels = [p["label"] for p in tab["parts"] if p["key"] in keys]
        if labels:
            groups.append(f"{tab['label']} ({g.join_names(labels)})")
    return g.join_names(groups)


def statement(session: Session) -> str:
    """Return the statement, or an empty string when the log shows no AI use for the paper."""
    events = [e for e in _events(session) if e["part_key"] not in OUTSIDE_THE_PAPER]
    text_events = [e for e in events if e["kind"] in REPORT_KINDS]
    kept_images = sum(1 for k in {e["part_key"] for e in events if e["kind"] == "image"} for f in session.attachments(k) if f["ai"])
    if not text_events and not kept_images:
        return ""
    sentences = []
    if text_events:
        models = sorted({_model(e["model"]) for e in text_events})
        sentences.append(f"In preparing this report, the researchers used {g.join_names(models)}, a generative artificial intelligence tool accessed through {PROVIDER_NAME}.")
        uses = []
        for kind, phrase in (("write", "draft text for"), ("diagram", "propose diagram contents for"), ("suggest", "suggest ideas for"), ("check", "review drafts of")):
            keys = {e["part_key"] for e in text_events if e["kind"] == kind}
            if keys:
                uses.append(f"{phrase} {_grouped(keys)}")
        sentences.append(f"The tool was used to {g.join_names(uses)}.")
        latest = {e["part_key"]: e["outcome"] for e in text_events if e["kind"] == "write"}
        edited = sum(1 for outcome in latest.values() if outcome == "edited")
        if edited:
            sentences.append(f"The researchers edited {edited} of the {len(latest)} AI-drafted parts after they were generated.")
    if kept_images:
        models = sorted({_model(e["model"]) for e in events if e["kind"] == "image"})
        sentences.append(f"{g.join_names(models)} was used to create {kept_images} concept mockup{'s' if kept_images != 1 else ''}, each labeled as AI-generated in its figure caption.")
    sentences.append(RESPONSIBILITY)
    return " ".join(sentences)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_confirmed(session: Session) -> bool:
    """True only for the exact statement confirmed, so later AI use requires a new confirmation."""
    text = statement(session)
    return bool(text) and session.get_meta(META_KEY) == _digest(text)


def confirm(session: Session, confirmed: bool) -> None:
    session.set_meta(META_KEY, _digest(statement(session)) if confirmed else "")


LABELS = {"write": "Drafted text", "check": "Reviewed a draft", "suggest": "Suggested ideas", "diagram": "Proposed a diagram", "image": "Created an image", "defense": "Drafted defense outline"}
OUTCOMES = {"generated": "Not yet reviewed", "accepted": "Accepted", "edited": "Edited by the researchers"}


def log_rows(session: Session) -> list[dict]:
    rows = []
    for event in _events(session):
        part = spec_module.part(event["part_key"]) or {}
        rows.append({
            "part": part.get("label", event["part_key"]), "use": LABELS.get(event["kind"], event["kind"]), "model": _model(event["model"]),
            "outcome": OUTCOMES.get(event["outcome"], event["outcome"]) if event["kind"] in ("write", "diagram") else "", "when": event["created_at"][:10],
            "neurons": json.loads(event["usage"] or "{}").get("neurons"),
        })
    return rows
