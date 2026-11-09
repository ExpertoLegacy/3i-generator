"""Write relay/prompts/parts.json from spec/ai.yaml and the template's own guidelines.

Run after changing either file, then deploy the relay. Students need no reinstall for prompt changes.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.parts import spec  # noqa: E402

TARGET = ROOT / "relay" / "prompts" / "parts.json"
DEFAULT_OUTPUT = 1500
LONG_OUTPUT = 2600


def build() -> dict:
    config = spec.ai_spec()
    parts = {}
    for key, entry in config["parts"].items():
        part = spec.part(key)
        if part is None:
            raise SystemExit(f"spec/ai.yaml names an unknown part: {key}")
        field_ids = {f["id"] for f in part["inputs"]}
        for name in [*entry.get("requires", []), *entry.get("suggest", {})]:
            if name not in field_ids:
                raise SystemExit(f"spec/ai.yaml: {key} has no field {name}")
        modes = list(entry.get("modes", []))
        if entry.get("suggest"):
            modes.append("suggest")
        diagram_kinds = (part.get("attach") or {}).get("diagram") or []
        if diagram_kinds:
            modes.append("diagram")
        if entry.get("auto_research"):
            modes.append("topics")
        auto = entry.get("auto_draft")
        if auto:
            modes.append(auto.get("mode", "auto") if isinstance(auto, dict) else "auto")
        if isinstance(auto, dict) and auto.get("fill"):
            modes.append("rows")
        if isinstance(auto, dict) and auto.get("suggest_choices"):
            modes.append("options")
        parts[key] = {
            "label": part["label"],
            "modes": modes,
            "tense": spec.ai_tense(key),
            "guideline": "\n".join(spec.guidance_for(key).get("guidelines") or []),
            "rules": " ".join(t for t in (entry.get("rules", ""), entry.get("auto_rules", "")) if t),
            "options_rule": entry.get("options_rule", ""),
            "suggest": {name: item["ask"] for name, item in entry.get("suggest", {}).items()},
            "max_output": LONG_OUTPUT if entry.get("long") else DEFAULT_OUTPUT,
            "diagram_kinds": diagram_kinds,
            "topics_rule": (entry.get("auto_research") or {}).get("rule", ""),
            "guided_questions": (entry.get("guided") or {}).get("questions", "3 to 5"),
        }
    base = (ROOT / "relay" / "prompts" / "base.js").read_text(encoding="utf-8")
    digest = hashlib.sha256((base + json.dumps(parts, sort_keys=True)).encode("utf-8")).hexdigest()[:10]
    return {"version": digest, "parts": parts}


def main() -> None:
    TARGET.write_text(json.dumps(build(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {TARGET}")


if __name__ == "__main__":
    main()
