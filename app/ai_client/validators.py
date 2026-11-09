"""Rule checks on AI text. Findings are shown to the student beside the draft."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.sources import apa

NUMBER = re.compile(r"(?<![\w.])\d+(?:,\d{3})*(?:\.\d+)?(?!\w)")
REF = re.compile(r"\{\{ref:(\w+):([\w.]+)\}\}")
ORDINAL_CONTEXT = re.compile(r"(?:Phase|Chapter|Appendix|Table|Figure|Section|Step|Grade|Question|Objective|Module|Version|Sprint|Level|Week)\s*$", re.IGNORECASE)
STANDARD_NUMBERS = {25010.0, 10173.0, 2012.0, 39.0, 2016.0}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
TEENS = {"eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
UNITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
SPELLED = re.compile(r"\b(" + "|".join(TENS) + r")(?:[- ](" + "|".join(UNITS) + r"))?\b|\b(" + "|".join(TEENS) + r")\b", re.IGNORECASE)
FILLER = re.compile(r"\bTODO\b|lorem ipsum|\bXX+\b|\binsert\b[^.]{0,30}\bhere\b|\bto be (?:added|provided|determined)\b", re.IGNORECASE)
QUOTE = re.compile(r"[“\"]([^”\"]{25,})[”\"]")


@dataclass
class Facts:
    payload_text: str
    sources: list[dict]
    manual_refs: str = ""
    tense: str = "free"
    words: list[int] | None = None
    tables: list[str] = field(default_factory=list)
    valid_refs: set[str] = field(default_factory=set)
    off_appendices: set[str] = field(default_factory=set)


def to_value(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def numbers_in(text: str) -> set[float]:
    found = {to_value(m.group(0)) for m in NUMBER.finditer(text)}
    found.discard(None)
    found |= tagalog_numbers(text)
    for tens, unit, teen in SPELLED.findall(text):
        found.add(float(TEENS[teen.lower()]) if teen else float(TENS[tens.lower()] + (UNITS[unit.lower()] if unit else 0)))
    return found


TL_UNITS = {"isa": 1, "isang": 1, "dalawa": 2, "dalawang": 2, "tatlo": 3, "tatlong": 3, "apat": 4, "lima": 5, "limang": 5, "anim": 6, "pito": 7, "pitong": 7, "walo": 8, "walong": 8, "siyam": 9}
TL_TENS = {"sampu": 10, "sampung": 10, "dalawampu": 20, "tatlumpu": 30, "apatnapu": 40, "limampu": 50, "animnapu": 60, "pitumpu": 70, "walumpu": 80, "siyamnapu": 90}
TL_TEENS = {"labing-isa": 11, "labindalawa": 12, "labintatlo": 13, "labing-apat": 14, "labinlima": 15, "labing-anim": 16, "labimpito": 17, "labingwalo": 18, "labinsiyam": 19}
TL_NUMBER = re.compile(
    r"\b(?:(" + "|".join(TL_TENS) + r")(?:ng|['’]?t)?(?:\s+(" + "|".join(TL_UNITS) + r"))?|(" + "|".join(TL_TEENS) + r")|(" + "|".join(TL_UNITS) + r")(?:\s+(?:na\s+)?(daan|libo))?)\b",
    re.IGNORECASE,
)


def tagalog_numbers(text: str) -> set[float]:
    """A student who writes apatnapu has given the number 40, so a draft saying 40 is not invented."""
    found: set[float] = set()
    for tens, tens_unit, teen, unit, scale in TL_NUMBER.findall(text):
        if tens:
            found.add(float(TL_TENS[tens.lower()] + (TL_UNITS[tens_unit.lower()] if tens_unit else 0)))
        elif teen:
            found.add(float(TL_TEENS[teen.lower()]))
        else:
            base = TL_UNITS[unit.lower()]
            found.add(float(base * {"": 1, "daan": 100, "libo": 1000}[scale.lower()]))
    return found


def unknown_numbers(text: str, allowed: set[float]) -> list[str]:
    body = apa.CITATION.sub(" ", REF.sub(" ", text))
    unknown: list[str] = []
    for match in NUMBER.finditer(body):
        value = to_value(match.group(0))
        if value is None or value in allowed or value in STANDARD_NUMBERS:
            continue
        if ORDINAL_CONTEXT.search(body[:match.start()]):
            continue
        if match.group(0) not in unknown:
            unknown.append(match.group(0))
    for match in SPELLED.finditer(body):
        tens, unit, teen = match.groups()
        value = float(TEENS[teen.lower()]) if teen else float(TENS[tens.lower()] + (UNITS[unit.lower()] if unit else 0))
        if value not in allowed and match.group(0) not in unknown:
            unknown.append(match.group(0))
    return unknown


def check_text(text: str, facts: Facts, citations_used: list[str] | None = None) -> list[dict]:
    issues: list[dict] = []

    def add(rule: str, message: str) -> None:
        issues.append({"rule": rule, "message": message})

    for name, year in sorted(apa.find_citations(text)):
        if not apa.citation_exists(name, year, facts.sources, facts.manual_refs):
            add("V1", f"Possibly invented citation: {name}, {year} is not in your Source Library. Remove it or add the real source.")
    for used in citations_used or []:
        found = apa.find_citations(f"({used})")
        if found and not all(apa.citation_exists(n, y, facts.sources, facts.manual_refs) for n, y in found) and not any(n in i["message"] and y in i["message"] for n, y in found for i in issues):
            add("V1", f"The AI says it used a source that is not in your Source Library: {used}.")

    allowed = numbers_in(facts.payload_text)
    unknown = unknown_numbers(text, allowed)
    if unknown:
        add("V2", f"These numbers are not in your answers or computed results: {', '.join(unknown)}. Confirm or remove them.")

    plain_payload = " ".join(facts.payload_text.split()).lower()
    for quote in QUOTE.findall(text):
        if " ".join(quote.split()).lower() not in plain_payload:
            add("V2", f"This quotation is not in your answers: “{quote[:60]}”. Remove it or quote the real source.")

    without_refs = REF.sub(" ", text)
    if "{{" in without_refs or "}}" in without_refs:
        add("V3", "Unfinished placeholder text remains. Replace anything inside double braces.")
    if re.search(r"\[[^\]]{1,60}\]", without_refs) or FILLER.search(without_refs):
        add("V3", "Placeholder wording remains. Replace it with your real information.")

    if facts.words:
        count = len(text.split())
        if not facts.words[0] <= count <= facts.words[1]:
            add("V4", f"This text has {count} words. It should have {facts.words[0]} to {facts.words[1]}.")

    if re.search(r"\b(?:I|we|our|We|Our|my|My)\b", without_refs):
        add("V5", "Replace I, we, our, or my with the third person.")
    if facts.tense == "past" and re.search(r"\b(?:will|shall)\s+\w+", without_refs, re.IGNORECASE):
        add("V5", "Some sentences use the future tense. A final report describes completed work in the past tense.")

    for token in facts.tables:
        if token not in text:
            add("V7", f"Mention the table ‹{token[6:-2]}› in your text before it appears.")
    for kind, name in REF.findall(text):
        token = f"{{{{ref:{kind}:{name}}}}}"
        if kind == "appendix" and f"app.{name}" in facts.off_appendices:
            add("V8", f"The text refers to an appendix you left out: {name}.")
        elif kind != "rq" and token not in facts.valid_refs:
            add("V8", f"The text refers to something that does not exist: ‹{kind}:{name}›.")
    return issues
