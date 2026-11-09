"""Fixed report content that comes from the template, reused by Fill generators."""
from __future__ import annotations

LIKERT = [
    (5, 4.21, 5.00, "Strongly Agree", "Highly Acceptable"),
    (4, 3.41, 4.20, "Agree", "Acceptable"),
    (3, 2.61, 3.40, "Neutral", "Moderately Acceptable"),
    (2, 1.81, 2.60, "Disagree", "Slightly Acceptable"),
    (1, 1.00, 1.80, "Strongly Disagree", "Not Acceptable"),
]

BOTH = "IT Experts and End-Users"
EXPERTS = "IT Experts"
ISO_2011 = [
    ("Functional Suitability", "Whether the system provides complete, correct, and appropriate functions that meet user needs", BOTH),
    ("Performance Efficiency", "Response time, use of resources, and capacity of the system", BOTH),
    ("Compatibility", "Ability to work with other systems, devices, or browsers and share the same environment", EXPERTS),
    ("Usability", "Ease of learning and use, protection from user errors, attractive interface, and accessibility", BOTH),
    ("Reliability", "Ability to work without failure, stay available, and recover from errors", BOTH),
    ("Security", "Protection of data through passwords, user roles, and controlled access", BOTH),
    ("Maintainability", "Ease of modifying, testing, and reusing the system’s code and modules", EXPERTS),
    ("Portability", "Ease of installing and running the system in other environments or devices", EXPERTS),
]

ISO_2023 = [
    ISO_2011[0], ISO_2011[1], ISO_2011[2],
    ("Interaction Capability", "Ease of learning and operating the system, protection from user errors, and an inclusive, engaging interface", BOTH),
    ISO_2011[4], ISO_2011[5], ISO_2011[6],
    ("Flexibility", "Ability to adapt to changes in requirements or environments, including installing, replacing, and scaling the system", EXPERTS),
    ("Safety", "Ability to avoid putting people, property, or the environment at unacceptable risk, including fail-safe behavior and hazard warnings", EXPERTS),
]
ISO_RENAMED = {"Usability": "Interaction Capability", "Portability": "Flexibility"}


def iso_characteristics(edition: str = "2011") -> list[tuple[str, str, str]]:
    return ISO_2023 if edition == "2023" else ISO_2011


DEFAULT_RESEARCH_QUESTIONS = ["problems", "features", "development", "acceptability", "difference", "enhancements"]

GANTT_ACTIVITIES = [
    ("Brainstorming; approval of research title (LA I)", [0]),
    ("Writing Chapter 1; proposal defense (LA II)", [1]),
    ("Review of related literature and systems (LA III)", [2]),
    ("Methodology; requirements gathering and design (LA IV)", [3]),
    ("System development and testing", [3, 4]),
    ("Evaluation; data analysis and interpretation (LA V)", [4]),
    ("Writing Chapters 4–5 and full report (LA VI)", [5]),
    ("Oral defense, revision, final submission (LA VII)", [6]),
]
GANTT_PERIODS = ["Wk 1–2", "Wk 3–5", "Wk 6–10", "Wk 11–13", "Wk 14–16", "Wk 17–18", "Wk 19–20"]


def verbal_interpretation(value: float) -> str:
    rounded = round(value + 1e-9, 2)
    for _scale, low, high, _response, verbal in LIKERT:
        if low <= rounded <= high:
            return verbal
    raise ValueError(f"Mean outside 1 to 5: {value}")


def iso_table(edition: str = "2011") -> dict:
    return {
        "type": "table",
        "caption_id": "iso",
        "title": "ISO/IEC 25010 Software Quality Characteristics Used in the Evaluation",
        "columns": [
            {"label": "Characteristic", "width": 2.2},
            {"label": "What It Measures", "width": 4},
            {"label": "Suggested Evaluators", "width": 1.8},
        ],
        "rows": [list(row) for row in iso_characteristics(edition)],
    }


def likert_table() -> dict:
    return {
        "type": "table",
        "caption_id": "likert",
        "title": "Likert Scale and Verbal Interpretation",
        "columns": [
            {"label": "Scale", "width": 0.8, "align": "center"},
            {"label": "Range", "width": 1.4, "align": "center"},
            {"label": "Response", "width": 2, "align": "center"},
            {"label": "Verbal Interpretation", "width": 2.4, "align": "center"},
        ],
        "rows": [[str(s), f"{lo:.2f} – {hi:.2f}", resp, verbal] for s, lo, hi, resp, verbal in LIKERT],
    }


def statistics_blocks(include_difference: bool = True) -> list[dict]:
    blocks: list[dict] = [
        {"type": "paragraph", "html": "<b>Frequency and Percentage.</b> Used to describe the profile of the respondents and the pass rate of the test cases."},
        {"type": "formula", "text": "P = (f / N) × 100"},
        {"type": "note", "text": "where P = percentage; f = frequency; N = total number of respondents or test cases"},
        {"type": "paragraph", "html": "<b>Weighted Mean.</b> Used to determine the level of acceptability of the system in each ISO/IEC 25010 characteristic (research question {{ref:rq:acceptability}})."},
        {"type": "formula", "text": "WM = Σ(f × x) / N"},
        {"type": "note", "text": "where WM = weighted mean; f = frequency of each response; x = weight of each scale point; N = total number of respondents"},
        {"type": "paragraph", "text": "The weighted mean is interpreted using the scale in {{ref:table:likert}}. The class interval of 0.80 is computed as (5 − 1) ÷ 5."},
        likert_table(),
    ]
    if include_difference:
        blocks.append(
            {"type": "paragraph", "html": "<b>t-test for Independent Samples.</b> Used to test whether there is a significant difference between the evaluations of the IT experts and the end-users at the 0.05 level of significance (research question {{ref:rq:difference}}). If the data are not normally distributed or the groups are very small, the Mann-Whitney U test may be used instead."}
        )
    return blocks
