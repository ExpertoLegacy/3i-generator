"""Replace personal data with placeholders before text leaves the computer, and put it back after."""
from __future__ import annotations

import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
LRN = re.compile(r"(?<!\d)\d{12}(?!\d)")
PHONE = re.compile(r"(?<![\d.])(?:(?:\+?63[\s-]?|0)9\d{2}[\s-]?\d{3}[\s-]?\d{4}|\(0\d{1,2}\)\s?\d{3}[\s-]?\d{4})(?!\d)")
TITLED = re.compile(r"\b(?:G|Gng|Bb)\.\s+[A-Z][\w'’-]+(?:\s+[A-Z][\w'’.-]*){0,3}|\b(?:Ginoong|Ginang|Binibining|Mang|Aling|Kuya|Ate|Tita|Tito)\s+[A-Z][\w'’-]+(?:\s+[A-Z][\w'’.-]*){0,2}|\b(?:Mr|Mrs|Ms|Dr|Prof|Engr|Atty)\.?\s+[A-Z][\w'’-]+(?:\s+[A-Z][\w'’.-]*){0,3}|\b(?:Sir|Ma['’]?am|Madam)\s+[A-Z][\w'’-]+(?:\s+[A-Z][\w'’.-]*){0,2}")
PLACEHOLDER = re.compile(r"\{\{\s*([A-Z]+(?:_\d+)?)\s*\}\}")

SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}
PARTICLES = {"de", "del", "dela", "delos", "la", "las", "los", "san", "santa", "sta", "sta."}
# Keys whose values must reach the model unchanged.
PROTECTED_KEYS = {"cite_as", "field", "ref", "kind", "row_keys"}


def name_variants(name: str) -> tuple[str, str]:
    """Return (first name, surname) from a Filipino-style full name."""
    tokens = [t for t in name.split() if t.lower() not in SUFFIXES]
    if len(tokens) < 2:
        return "", ""
    index = len(tokens) - 1
    while index > 1 and tokens[index - 1].lower() in PARTICLES:
        index -= 1
    return tokens[0], " ".join(tokens[index:])


class Masker:
    def __init__(self, keep_surnames: set[str] | None = None):
        self.keep = {s.lower() for s in (keep_surnames or set())}
        self.table: dict[str, str] = {}
        self.exact: list[tuple[str, str]] = []
        self.loose: list[str] = []
        self.counts: dict[str, int] = {}
        self.surfaces: dict[str, str] = {}
        self.contacts_found = 0

    def _next(self, prefix: str) -> str:
        self.counts[prefix] = self.counts.get(prefix, 0) + 1
        return f"{{{{{prefix}_{self.counts[prefix]}}}}}"

    def add(self, name: str | None, placeholder: str, variants: bool = True) -> None:
        name = " ".join((name or "").split())
        if not name or any(existing.lower() == name.lower() for existing, _ in self.exact):
            return
        token = f"{{{{{placeholder}}}}}"
        self.table[token] = name
        self.exact.append((name, token))
        if not variants:
            return
        first, surname = name_variants(name)
        if not surname:
            return
        self.loose.append(f"{first} {surname}")
        if surname.lower() not in self.keep and len(surname) >= 3:
            self.loose.append(surname)
        if len(first) >= 3 and first.lower() not in self.keep:
            self.loose.append(first)

    def _surface(self, text: str, prefix: str) -> str:
        if text not in self.surfaces:
            token = self._next(prefix)
            self.surfaces[text] = token
            self.table[token] = text
        return self.surfaces[text]

    def _contact(self, prefix: str):
        def replace(match: re.Match) -> str:
            self.contacts_found += 1
            return self._surface(match.group(0), prefix)
        return replace

    def mask(self, text: str) -> str:
        text = EMAIL.sub(self._contact("EMAIL"), text)
        text = LRN.sub(self._contact("LRN"), text)
        text = PHONE.sub(self._contact("PHONE"), text)
        for name, token in sorted(self.exact, key=lambda item: -len(item[0])):
            text = re.sub(r"(?<![\w])" + r"\s+".join(re.escape(p) for p in name.split()) + r"(?![\w])", token, text, flags=re.IGNORECASE)
        for surface in sorted(set(self.loose), key=len, reverse=True):
            # Case-sensitive so that a name like Grace does not catch the word grace.
            text = re.sub(r"(?<![\w{])" + r"\s+".join(re.escape(p) for p in surface.split()) + r"(?![\w}])", lambda m: self._surface(m.group(0), "NAME"), text)
        return TITLED.sub(lambda m: self._surface(m.group(0), "NAME"), text)

    def mask_tree(self, value, key: str = ""):
        if key in PROTECTED_KEYS:
            return value
        if isinstance(value, str):
            return self.mask(value)
        if isinstance(value, list):
            return [self.mask_tree(item) for item in value]
        if isinstance(value, dict):
            return {k: self.mask_tree(v, k) for k, v in value.items()}
        return value

    def restore(self, text: str) -> str:
        return PLACEHOLDER.sub(lambda m: self.table.get(f"{{{{{m.group(1)}}}}}", m.group(0)), text)
