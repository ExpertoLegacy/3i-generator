"""APA 7 references, citation checks, and online source lookup (online only, title or DOI only)."""
from __future__ import annotations

import re
from datetime import date
from html import escape

import httpx

from app import net

TIMEOUT = 12
USER_AGENT = "ResearchReportGenerator/1.0"


def split_authors(text: str | None) -> list[str]:
    return [a.strip() for a in (text or "").split(";") if a.strip()]


def format_authors(authors: list[str]) -> str:
    if not authors:
        return ""
    if len(authors) == 1:
        return authors[0]
    if len(authors) <= 20:
        return ", ".join(authors[:-1]) + ", & " + authors[-1]
    return ", ".join(authors[:19]) + ", . . . " + authors[-1]


def _x(value) -> str:
    return escape(str(value or "").strip(), quote=False)


def _period(text: str) -> str:
    return text if not text or text[-1] in ".?!" else text + "."


def format_reference(source: dict) -> str:
    """Return one APA 7 reference as limited HTML."""
    authors = _x(format_authors(split_authors(source.get("authors"))))
    year = _x(source.get("year")) or "n.d."
    title = _x(source.get("title"))
    container = _x(source.get("container"))
    link = _x(source.get("link"))
    kind = (source.get("type") or "Journal article").lower()
    lead = f"{_period(authors)} ({year})." if authors else f"{title}. ({year})."
    if kind.startswith("journal"):
        volume = _x(source.get("volume"))
        issue = _x(source.get("issue"))
        pages = _x(source.get("pages"))
        where = f"<i>{container}</i>" if container else ""
        if volume:
            where += f", <i>{volume}</i>" + (f"({issue})" if issue else "")
        if pages:
            where += f", {pages}"
        body = f"{_period(title)} {where}." if where else _period(title)
    elif kind.startswith("book"):
        body = f"<i>{_period(title)}</i> {_period(container)}".strip()
    elif kind.startswith("thesis"):
        body = f"<i>{title}</i> [Unpublished thesis]. {_period(container)}".strip()
    else:
        body = f"<i>{_period(title)}</i> {_period(container)}".strip()
    if not authors:
        body = body.replace(_period(title), "", 1).strip() if not kind.startswith("journal") else ""
    return " ".join(part for part in (lead, body, link) if part)


def surname(source: dict) -> str:
    authors = split_authors(source.get("authors"))
    if not authors:
        return (source.get("title") or "").split(" ")[0]
    return authors[0].split(",")[0].strip()


def is_cited(source: dict, text: str) -> bool:
    name = surname(source)
    year = str(source.get("year") or "").strip()
    if not name or not year:
        return False
    return re.search(rf"\b{re.escape(name)}\b.{{0,60}}?\b{re.escape(year)}\b", text) is not None


CITATION = re.compile(
    r"(?:\(|\b)((?:[A-Z][\w'’\-]+\s)?[A-Z][\w'’\-]+)(?:\s+et al\.|\s+(?:&|and)\s+[A-Z][\w'’\-]+)?(?:,\s*|\s+\()(\d{4})[a-z]?\)"
)
IGNORE_NAMES = {"Table", "Figure", "Act", "Order", "Appendix", "No", "Agenda"}


def find_citations(text: str) -> set[tuple[str, str]]:
    found = set()
    for name, year in CITATION.findall(text):
        last = name.split()[-1]
        if last not in IGNORE_NAMES and 1900 <= int(year) <= 2100:
            found.add((name, year))
    return found


def citation_exists(name: str, year: str, sources: list[dict], manual: str) -> bool:
    last = name.split()[-1]
    for source in sources:
        if str(source.get("year") or "").strip() == year and last in (source.get("authors") or source.get("title") or ""):
            return True
    return re.search(rf"\b{re.escape(last)}\b.{{0,300}}?\({year}", manual) is not None


# ---- online lookup -------------------------------------------------------------------


class LookupError_(Exception):
    pass


def _crossref_to_source(item: dict) -> dict:
    authors = []
    for person in item.get("author", [])[:20]:
        family = person.get("family") or person.get("name") or ""
        initials = " ".join(f"{p[0]}." for p in re.split(r"[\s\-]+", person.get("given") or "") if p)
        authors.append(f"{family}, {initials}".strip(", "))
    published = item.get("issued", {}).get("date-parts", [[None]])[0]
    kind = "Book" if "book" in (item.get("type") or "") else "Journal article"
    return {
        "authors": "; ".join(authors), "year": str(published[0] or ""),
        "title": " ".join((item.get("title") or [""])[0].split()),
        "container": (item.get("container-title") or [item.get("publisher", "")])[0] if item.get("container-title") else item.get("publisher", ""),
        "volume": item.get("volume", ""), "issue": item.get("issue", ""), "pages": item.get("page", ""),
        "link": f"https://doi.org/{item['DOI']}" if item.get("DOI") else item.get("URL", ""),
        "type": kind, "origin": "Foreign",
    }


SEARCH_URL = "https://api.openalex.org/works"
SEARCH_FIELDS = "title,publication_year,doi,type,authorships,primary_location,biblio,abstract_inverted_index"
SEARCH_YEARS = 10
NOTE_CHARS = 1500
PHILIPPINE = re.compile(r"\bphilippines?\b|\bphilippine\b|\bfilipino\b|\bmanila\b|\bluzon\b|\bmindanao\b|\bvisayas\b|\bdeped\b", re.IGNORECASE)


def _abstract(index: dict | None) -> str:
    if not index:
        return ""
    words = sorted((position, word) for word, positions in index.items() for position in positions)
    return " ".join(word for _, word in words)[:NOTE_CHARS]


def _openalex_author(name: str) -> str:
    first, last = name_parts(name)
    initials = " ".join(f"{p[0]}." for p in re.split(r"[\s\-]+", first) if p and p[0].isalpha())
    return f"{last}, {initials}".strip(", ")


def name_parts(name: str) -> tuple[str, str]:
    """Split a full name, keeping surname particles such as De Jesus or Dela Cruz together."""
    tokens = name.split()
    if len(tokens) < 2:
        return "", name
    index = len(tokens) - 1
    while index > 1 and tokens[index - 1].lower() in {"de", "del", "dela", "delos", "la", "las", "los", "san", "santa", "van", "von"}:
        index -= 1
    return " ".join(tokens[:index]), " ".join(tokens[index:])


def _openalex_to_source(work: dict) -> dict | None:
    authors = [a.get("author", {}).get("display_name") or "" for a in work.get("authorships", [])[:20]]
    authors = [a for a in authors if a]
    if not (work.get("title") and authors and work.get("publication_year")):
        return None
    countries = {c for a in work.get("authorships", []) for c in a.get("countries", [])}
    location = work.get("primary_location") or {}
    biblio = work.get("biblio") or {}
    pages = "–".join(p for p in (biblio.get("first_page"), biblio.get("last_page")) if p)
    abstract = _abstract(work.get("abstract_inverted_index"))
    container = (location.get("source") or {}).get("display_name") or ""
    local = "PH" in countries or PHILIPPINE.search(f"{work['title']} {container} {abstract}") is not None
    return {
        "authors": "; ".join(_openalex_author(a) for a in authors), "year": str(work["publication_year"]),
        "title": " ".join(re.sub(r"<[^>]+>", "", work["title"]).split()),
        "container": container,
        "volume": biblio.get("volume") or "", "issue": biblio.get("issue") or "", "pages": pages, "link": work.get("doi") or "",
        "type": "Book" if "book" in (work.get("type") or "") else "Journal article", "origin": "Local" if local else "Foreign",
        "notes": abstract,
    }


def search_studies(query: str, client: httpx.Client | None = None) -> list[dict]:
    """Find real published studies from the last 10 years. Nothing here comes from an AI model."""
    query = query.strip()
    if not query:
        return []
    owns_client = client is None
    client = client or httpx.Client(transport=net.transport(), timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    since = date.today().year - SEARCH_YEARS
    try:
        response = client.get(SEARCH_URL, params={"search": query, "per-page": 15, "select": SEARCH_FIELDS, "filter": f"from_publication_date:{since}-01-01,is_retracted:false"})
        response.raise_for_status()
        found = [s for s in (_openalex_to_source(w) for w in response.json().get("results", [])) if s]
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise LookupError_("Source search needs an internet connection. You can still type the source yourself.") from exc
    finally:
        if owns_client:
            client.close()
    return sorted(found, key=lambda s: not s["notes"])[:8]  # a study with an abstract can actually be read and used


def lookup(query: str, client: httpx.Client | None = None) -> list[dict]:
    query = query.strip()
    if not query:
        return []
    owns_client = client is None
    client = client or httpx.Client(transport=net.transport(), timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    doi = re.search(r"10\.\d{4,9}/\S+", query)
    try:
        if doi:
            response = client.get(f"https://api.crossref.org/works/{doi.group(0)}")
            if response.status_code == 404:
                return []
            response.raise_for_status()
            return [_crossref_to_source(response.json()["message"])]
        response = client.get("https://api.crossref.org/works", params={"query.bibliographic": query, "rows": 6})
        response.raise_for_status()
        return [_crossref_to_source(item) for item in response.json()["message"]["items"]]
    except httpx.HTTPError as exc:
        raise LookupError_("Source search needs an internet connection. You can still type the source yourself.") from exc
    finally:
        if owns_client:
            client.close()
