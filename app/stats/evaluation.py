"""Import evaluation responses and compute results. All statistics come from code, never AI.

Rules follow project context section 7. Names and other identity columns are never stored:
the raw table stays in memory only until the student applies a column mapping.
"""
from __future__ import annotations

import io
import math
import re

import pandas as pd
from scipy import stats

LIKERT_WORDS = {"strongly agree": 5, "agree": 4, "neutral": 3, "undecided": 3, "disagree": 2, "strongly disagree": 1}
IDENTITY = re.compile(r"name|e-?mail|timestamp|phone|contact|lrn|address|signature|section", re.I)
GROUP = re.compile(r"respondent|group|type of|category|you are|evaluator", re.I)


class ImportError_(Exception):
    pass


def read_table(raw: bytes, filename: str) -> pd.DataFrame:
    try:
        if filename.lower().endswith((".xlsx", ".xlsm")):
            frame = pd.read_excel(io.BytesIO(raw), engine="openpyxl", dtype=object)
        elif filename.lower().endswith(".csv"):
            frame = pd.read_csv(io.BytesIO(raw), dtype=object, encoding="utf-8-sig")
        else:
            raise ImportError_("Use a CSV or Excel (.xlsx) file.")
    except (ValueError, UnicodeDecodeError, pd.errors.ParserError, OSError) as exc:
        raise ImportError_("This file could not be read. Check that it is a valid CSV or Excel file.") from exc
    frame = frame.dropna(how="all")
    if frame.empty:
        raise ImportError_("This file has no responses.")
    return frame


def rating(value) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip().lower()
    if text in LIKERT_WORDS:
        return float(LIKERT_WORDS[text])
    match = re.match(r"^([1-5])(\.0+)?\b", text)
    return float(match.group(1)) if match else None


def group_of(value) -> str | None:
    text = str(value or "").lower()
    if "expert" in text or re.search(r"\bit\b", text):
        return "experts"
    if text.strip() and text != "nan":
        return "users"
    return None


def guess_mapping(columns: list[str], characteristics: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    group_taken = False
    for column in columns:
        lowered = column.lower()
        target = next((c for c in characteristics if c.lower() in lowered), None)
        if target:
            mapping[column] = target
        elif not group_taken and GROUP.search(column) and not IDENTITY.search(column):
            mapping[column] = "__group__"
            group_taken = True
        else:
            mapping[column] = ""
    return mapping


def clean(frame: pd.DataFrame, mapping: dict[str, str]) -> dict:
    """Keep only group and ratings. Unmapped columns, including names, are discarded here."""
    group_columns = [c for c, target in mapping.items() if target == "__group__"]
    if len(group_columns) != 1:
        raise ImportError_("Choose exactly one column that says whether each respondent is an IT expert or an end-user.")
    rating_columns = {c: t for c, t in mapping.items() if t and t != "__group__"}
    if not rating_columns:
        raise ImportError_("Match at least one question column to a quality characteristic.")
    missing = [c for c in [*group_columns, *rating_columns] if c not in frame.columns]
    if missing:
        raise ImportError_("The mapping does not match this file. Import the file again.")
    respondents = []
    skipped = 0
    for _, row in frame.iterrows():
        group = group_of(row[group_columns[0]])
        ratings: dict[str, list[float]] = {}
        for column, characteristic in rating_columns.items():
            value = rating(row[column])
            if value is None:
                if pd.notna(row[column]) and str(row[column]).strip():
                    skipped += 1
                continue
            ratings.setdefault(characteristic, []).append(value)
        if group and ratings:
            respondents.append({"group": group, "ratings": ratings})
    if not respondents:
        raise ImportError_("No usable responses were found. Ratings must be 1 to 5, or words such as Agree.")
    return {"respondents": respondents, "unreadable_answers": skipped}


def weighted_means(data: dict) -> dict:
    """Weighted mean per characteristic and group. Equivalent to Σ(f × x) / N."""
    collected: dict[str, dict[str, list[float]]] = {"experts": {}, "users": {}}
    for person in data["respondents"]:
        for characteristic, values in person["ratings"].items():
            collected[person["group"]].setdefault(characteristic, []).extend(values)
    means = {
        group: {c: sum(v) / len(v) for c, v in by_characteristic.items()}
        for group, by_characteristic in collected.items()
    }
    counts = {g: sum(1 for p in data["respondents"] if p["group"] == g) for g in ("experts", "users")}
    return {"means": means, "counts": counts}


def compare_groups(data: dict) -> dict | None:
    """Welch's t-test on each respondent's mean over characteristics rated by both groups."""
    summary = weighted_means(data)["means"]
    shared = sorted(set(summary["experts"]) & set(summary["users"]))
    if not shared:
        return None
    scores: dict[str, list[float]] = {"experts": [], "users": []}
    for person in data["respondents"]:
        values = [v for c in shared for v in person["ratings"].get(c, [])]
        if values:
            scores[person["group"]].append(sum(values) / len(values))
    a, b = scores["experts"], scores["users"]
    if len(a) < 2 or len(b) < 2:
        return None
    test = stats.ttest_ind(a, b, equal_var=False)
    result = {
        "experts_mean": f"{pd.Series(a).mean():.2f}", "experts_sd": f"{pd.Series(a).std(ddof=1):.2f}",
        "users_mean": f"{pd.Series(b).mean():.2f}", "users_sd": f"{pd.Series(b).std(ddof=1):.2f}",
        "t": f"{float(test.statistic):.2f}", "p": f"{float(test.pvalue):.3f}",
        "n_experts": len(a), "n_users": len(b), "shared": shared,
    }
    if min(len(a), len(b)) < 10:
        u = stats.mannwhitneyu(a, b, alternative="two-sided")
        result["mann_whitney_p"] = f"{float(u.pvalue):.3f}"
    return result


def pilot_alpha(frame: pd.DataFrame) -> dict:
    """Cronbach's alpha from pilot responses. Only rating columns are read, and no rows are kept."""
    columns = {}
    for column in frame.columns:
        if IDENTITY.search(str(column)) or GROUP.search(str(column)):
            continue
        values = [rating(v) for v in frame[column]]
        if sum(v is not None for v in values) >= len(values) * 0.8:
            columns[column] = values
    if len(columns) < 2:
        raise ImportError_("Reliability needs at least two rating questions, answered from 1 to 5.")
    complete = [row for row in zip(*columns.values()) if all(v is not None for v in row)]
    if len(complete) < 3:
        raise ImportError_("Reliability needs complete answers from at least three respondents.")
    items = [list(column) for column in zip(*complete)]
    if pd.Series([sum(row) for row in complete]).var(ddof=1) == 0:
        raise ImportError_("Every respondent gave the same total, so reliability cannot be computed.")
    return {"alpha": cronbach_alpha(items), "items": len(items), "respondents": len(complete)}


def cronbach_alpha(items: list[list[float]]) -> float:
    """Items are columns of equal length, one list per question."""
    k = len(items)
    if k < 2:
        raise ValueError("Cronbach's alpha needs at least two items")
    item_variance = sum(pd.Series(i).var(ddof=1) for i in items)
    totals = [sum(values) for values in zip(*items)]
    return k / (k - 1) * (1 - item_variance / pd.Series(totals).var(ddof=1))
