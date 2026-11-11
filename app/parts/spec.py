"""Load the tab spec and template guidance."""
from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path

import yaml

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))


RESEARCH_TOPICS = {
    "id": "research_topics", "label": "Topics", "type": "repeater", "add": "Add a topic",
    "fields": [
        {"id": "topic", "label": "Topic", "type": "text"},
        {"id": "search", "type": "hidden"},  # database words the AI chose; cleared if the student rewrites the topic
    ],
}


@lru_cache
def spec() -> dict:
    data = yaml.safe_load((BASE / "spec" / "tabs.yaml").read_text(encoding="utf-8"))
    for tab in data["tabs"]:
        for part in tab["parts"]:
            part["tab"] = tab["id"]
            part.setdefault("inputs", [])
            part["attach"] = data.get("attachments", {}).get(part["key"])
            if ((ai_config(part["key"]) or {}).get("guided")):
                part["inputs"] = [{"id": "answers", "label": "Questions from the system", "type": "guided"}, {"id": "search_terms", "type": "hidden"}, *part["inputs"]]
            auto = (ai_config(part["key"]) or {}).get("auto_draft")
            if isinstance(auto, dict) and (auto.get("choices") or auto.get("suggest_choices")):
                part["inputs"] = [{"id": "choice", "type": "hidden"}, {"id": "options_json", "type": "hidden"}, *part["inputs"]]
            if part["key"].startswith("app."):
                part["optional"] = True
            if ((ai_config(part["key"]) or {}).get("auto_research")):
                part["inputs"] = [RESEARCH_TOPICS, *part["inputs"]]
    return data


@lru_cache
def guidelines() -> dict:
    return json.loads((BASE / "spec" / "guidelines.json").read_text(encoding="utf-8"))


@lru_cache
def ai_spec() -> dict:
    data = yaml.safe_load((BASE / "spec" / "ai.yaml").read_text(encoding="utf-8"))
    flow = yaml.safe_load((BASE / "spec" / "flow.yaml").read_text(encoding="utf-8"))
    for key, extra in flow["parts"].items():
        merged = data["parts"].setdefault(key, {"modes": ["write", "check"]})
        merged.update(extra)
    return data


@lru_cache
def translations() -> dict:
    return yaml.safe_load((BASE / "spec" / "translations.yaml").read_text(encoding="utf-8"))


@lru_cache
def fixed_values() -> dict:
    """Project Setup answers set by the school, which students cannot change."""
    found: dict = {}
    for item in all_parts():
        for field in item["inputs"]:
            if "fixed" in field:
                found.setdefault(item["key"], {})[field["id"]] = field["fixed"]
    return found


def with_fixed(key: str, data: dict) -> dict:
    fixed = fixed_values().get(key)
    return {**data, **fixed} if fixed else data


def auto_config(key: str) -> dict | None:
    """How a part is auto-drafted, or None if it is not."""
    value = (ai_config(key) or {}).get("auto_draft")
    return ({} if value is True else value) if value else None


def ai_config(key: str) -> dict | None:
    return ai_spec()["parts"].get(key)


def ai_tense(key: str) -> str:
    entry = ai_config(key) or {}
    return entry.get("tense") or ai_spec()["tense_by_tab"].get(part(key)["tab"], "free")


def tabs() -> list[dict]:
    return spec()["tabs"]


def all_parts() -> list[dict]:
    return [part for tab in tabs() for part in tab["parts"]]


def part(key: str) -> dict | None:
    return next((p for p in all_parts() if p["key"] == key), None)


def report_keys() -> list[str]:
    """Part keys that produce report content, in document order."""
    return [
        p["key"] for p in all_parts()
        if not p.get("setup") and not p.get("auto") and not p.get("view") and not p.get("no_output")
    ]


def guidance_for(key: str) -> dict:
    data = guidelines()
    result = dict(data.get(key, {}))
    if key.startswith("ch4.design.") and not result:
        result = dict(data.get("ch4.design", {}))
    return result
