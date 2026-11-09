"""A complete, fictional report used to test the builder. All people and data are invented."""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from app.docbuild.standard import (
    DEFAULT_RESEARCH_QUESTIONS,
    GANTT_ACTIVITIES,
    GANTT_PERIODS,
    iso_table,
    statistics_blocks,
    verbal_interpretation,
)

PROPONENTS = ["ALEX M. REYES", "BEA L. SANTOS", "CARLO D. VILLANUEVA"]
LOREM = (
    "The researchers observed that the current process depended on handwritten logbooks, which made "
    "it difficult for the science department to determine the status of laboratory equipment at any "
    "given time. Records were reconciled by hand at the end of each quarter, and missing entries were "
    "only discovered during inventory. "
)


def image(label: str, width: int = 1200, height: int = 700) -> bytes:
    picture = Image.new("RGB", (width, height), (238, 244, 251))
    draw = ImageDraw.Draw(picture)
    draw.rectangle((10, 10, width - 11, height - 11), outline=(46, 92, 138), width=6)
    draw.text((width // 2 - 80, height // 2), f"TEST IMAGE: {label}", fill=(31, 61, 99))
    out = io.BytesIO()
    picture.save(out, format="PNG", dpi=(200, 200))
    return out.getvalue()


def para(*html: str) -> list[dict]:
    return [{"type": "paragraph", "html": h} for h in html]


def facts() -> dict:
    title = "LABTRACK: A WEB-BASED LABORATORY EQUIPMENT INVENTORY AND BORROWING SYSTEM FOR THE SCIENCE DEPARTMENT OF CABIAO SENIOR HIGH SCHOOL"
    return {
        "subject": "Inquiries, Investigations and Immersion",
        "subject_upper": "INQUIRIES, INVESTIGATIONS AND IMMERSION",
        "research_theme": "System Software Development",
        "strand_section": "Grade 12 – TVL-ICT / Turing",
        "school_name": "Cabiao Senior High School",
        "school_address": "Palasinan, Cabiao, Nueva Ecija",
        "title": title,
        "title_lines": [
            "LABTRACK: A WEB-BASED LABORATORY",
            "EQUIPMENT INVENTORY AND BORROWING",
            "SYSTEM FOR THE SCIENCE",
            "DEPARTMENT OF CABIAO SENIOR",
            "HIGH SCHOOL",
        ],
        "proponents_upper": PROPONENTS,
        "proponents_joined": "Alex M. Reyes, Bea L. Santos, and Carlo D. Villanueva",
        "adviser_upper": "ERICKSON N. GLODO",
        "month_year": "March 2027",
        "panel_chair_upper": "DIANA P. CRUZ",
        "panel_member_1_upper": "EDUARDO F. RAMOS",
        "panel_member_2_upper": "FLORA G. MENDOZA",
        "principal_upper": "GABRIEL H. TORRES",
        "principal_title": "SHS Assistant Principal",
    }


def options_questions() -> list[str]:
    return list(DEFAULT_RESEARCH_QUESTIONS)


def acceptability_rows() -> list:
    data = [
        ("Functional Suitability", 4.60, 4.48), ("Performance Efficiency", 4.40, 4.31),
        ("Compatibility", 4.20, None), ("Usability", 4.70, 4.62), ("Reliability", 4.30, 4.27),
        ("Security", 4.50, 4.35), ("Maintainability", 4.10, None), ("Portability", 4.00, None),
    ]
    rows: list = []
    for name, experts, users in data:
        rows.append([name, f"{experts:.2f}", verbal_interpretation(experts),
                     f"{users:.2f}" if users else "—", verbal_interpretation(users) if users else "—"])
    rows.append({"bold": True, "cells": ["Overall Weighted Mean", "4.35", verbal_interpretation(4.35), "4.41", verbal_interpretation(4.41)]})
    return rows


def parts() -> dict[str, list[dict]]:
    centered = {"align": "center"}
    p: dict[str, list[dict]] = {}
    p["prelim.acknowledgment"] = para(
        "The researchers express their sincere gratitude to their research adviser and the panel of examiners for their guidance.",
        "They also thank the school head, the science department, the IT experts and end-users who evaluated the system, and their families.",
    )
    p["prelim.abstract"] = [
        {"type": "paragraph", "text": "This study designed, developed, and evaluated LabTrack, a web-based laboratory equipment inventory and borrowing system. The study used a developmental research design and the Agile model. The system was built using PHP and MySQL and was evaluated by IT experts and end-users using an instrument based on ISO/IEC 25010. The system obtained an overall weighted mean of 4.41, interpreted as Highly Acceptable. The researchers concluded that the system addressed the problems identified and recommend its adoption.", "indent": False},
        {"type": "paragraph", "html": "<b>Keywords:</b> web-based system, inventory management, ISO/IEC 25010, Agile, developmental research", "indent": False},
    ]
    p["ch1.background"] = para(LOREM * 3, LOREM * 2 + "This agrees with the study of Dela Cruz (2024).", LOREM * 2)
    p["ch1.sop"] = [
        {"type": "paragraph", "text": "This study aims to design, develop, and evaluate LabTrack, a web-based inventory and borrowing system for the science department of Cabiao Senior High School. Specifically, it seeks to answer the following questions:"},
        {"type": "list", "items": [
            "What are the problems encountered in the current borrowing process of the science department?",
            {"text": "What features and modules should the system have to address these problems in terms of:", "children": ["user account management;", "equipment inventory;", "borrowing and return; and", "report generation?"]},
            "How can the system be designed and developed using the Agile model?",
            {"text": "What is the level of acceptability of the developed system as evaluated by IT experts and end-users in terms of the ISO/IEC 25010 software quality characteristics?"},
            "Is there a significant difference between the evaluations of the IT experts and the end-users?",
            "What enhancements can be proposed based on the results of the evaluation?",
        ]},
    ]
    p["ch1.objectives"] = [
        {"type": "paragraph", "text": "The general objective of this study is to design, develop, and evaluate LabTrack. Specifically, it aims to:"},
        {"type": "list", "items": ["identify the problems encountered in the current process;", "determine the features and modules needed;", "design and develop the system using the Agile model;", "evaluate the level of acceptability of the system based on ISO/IEC 25010;", "determine whether there is a significant difference between the evaluations; and", "propose enhancements based on the evaluation results."]},
    ]
    p["ch1.hypothesis"] = para("<b>Ho:</b> There is no significant difference between the evaluations of the IT experts and the end-users on the level of acceptability of LabTrack at the 0.05 level of significance.")
    p["ch1.framework"] = [
        {"type": "paragraph", "text": "{{ref:figure:ipo}} presents the Input-Process-Output model of the study."},
        {"type": "figure", "caption_id": "ipo", "title": "Conceptual Framework of the Study", "data": image("IPO"), "width_in": 5.5},
    ]
    p["ch1.scope"] = para(LOREM * 2)
    p["ch1.significance"] = para(
        "<b>End-Users.</b> The system will let students and teachers borrow equipment without paper forms.",
        "<b>School Administration.</b> The system will give the office accurate and timely inventory reports.",
        "<b>Researchers.</b> The researchers will gain practical experience in building a complete system.",
        "<b>Future Researchers.</b> This study can serve as a reference for later system development studies.",
    )
    p["ch1.terms"] = para("<b>Module.</b> As used in this study, it refers to a functional part of LabTrack.", "<b>Usability.</b> As used in this study, it refers to how effectively end-users complete tasks.")

    p["ch2.literature"] = [{"type": "heading3", "text": "Inventory Management"}, *para(LOREM * 2), {"type": "heading3", "text": "Web-Based Information Systems"}, *para(LOREM * 2)]
    p["ch2.studies"] = [{"type": "heading3", "text": "Foreign Studies"}, *para(LOREM), {"type": "heading3", "text": "Local Studies"}, *para(LOREM)]
    p["ch2.systems"] = para(LOREM)
    p["ch2.synthesis"] = [
        *para("{{ref:table:features}} compares the related systems with the proposed system."),
        {"type": "table", "caption_id": "features", "title": "Comparison of Features of Related Systems and the Proposed System",
         "columns": [{"label": "Feature", "width": 3}, *[{"label": n, "width": 1.2, **centered} for n in ("System A", "System B", "System C", "LabTrack")]],
         "rows": [["User account management", "Yes", "Yes", "No", "Yes"], ["Equipment inventory", "Yes", "Yes", "Yes", "Yes"], ["Borrowing records", "No", "Yes", "No", "Yes"], ["Automated reports", "No", "No", "Yes", "Yes"], ["Overdue notices", "No", "No", "No", "Yes"]]},
    ]

    p["ch3.design"] = para(LOREM)
    p["ch3.method.overview"] = [
        *para("The researchers used the Agile model shown in {{ref:figure:model}}."),
        {"type": "figure", "caption_id": "model", "title": "Agile Software Development Model", "data": image("MODEL"), "width_in": 4.5},
    ]
    for number, name in enumerate(("Requirements Planning and Analysis", "System Design", "Development (Coding)", "Testing", "Deployment and Evaluation"), start=1):
        extra = {1: " The interview guide is in {{ref:appendix:interview}}.", 4: " The complete test cases are in {{ref:appendix:test_cases}}.", 5: " The user manual is in {{ref:appendix:manual}}."}.get(number, "")
        p[f"ch3.method.phase{number}"] = [{"type": "heading3", "text": f"Phase {number}: {name}"}, *para(LOREM + extra)]
    p["ch3.requirements"] = [
        *para("{{ref:table:hardware}} and {{ref:table:software}} present the requirements."),
        {"type": "table", "caption_id": "hardware", "title": "Hardware Requirements", "columns": [{"label": "Component"}, {"label": "Used in Development"}, {"label": "Minimum Requirement for Users"}],
         "rows": [["Processor", "Intel Core i5, 12th Gen", "Intel Core i3"], ["Memory (RAM)", "16 GB", "4 GB"], ["Storage", "512 GB SSD", "20 GB"], ["Display / Device", "14-inch laptop", "Any screen, 1366x768"], ["Network", "Wi-Fi", "Local network"]]},
        {"type": "table", "caption_id": "software", "title": "Software Requirements", "columns": [{"label": "Software"}, {"label": "Purpose", "width": 2}, {"label": "Version"}],
         "rows": [["Windows", "Platform used in development and deployment", "11"], ["PHP", "Main language of the system", "8.3"], ["MySQL", "Storage of system records", "8.0"], ["Visual Studio Code", "Writing and debugging the source code", "1.95"], ["Google Chrome", "Running and testing the system", "130"]]},
    ]
    p["ch3.locale"] = para(LOREM)
    p["ch3.population"] = [
        *para("{{ref:table:respondents}} shows the distribution of respondents."),
        {"type": "table", "caption_id": "respondents", "title": "Distribution of Respondents", "columns": [{"label": "Respondent Group", "width": 2}, *[{"label": n, **centered} for n in ("Population (N)", "Sample (n)", "Percentage")]],
         "rows": [["IT Experts", "5", "5", "100%"], ["End-Users: science teachers and students", "120", "92", "76.67%"], {"bold": True, "cells": ["Total", "125", "97", "77.60%"]}]},
    ]
    p["ch3.sampling"] = para(LOREM)
    p["ch3.instrument"] = [*para("The instrument is in {{ref:appendix:instrument}}, with validation in {{ref:appendix:validation}}. {{ref:table:iso}} lists the characteristics."), iso_table()]
    p["ch3.gathering"] = [{"type": "list", "items": ["Secured a letter of permission from the school principal ({{ref:appendix:permission}}).", "Conducted interviews and observation to gather the system requirements.", "Designed, developed, and tested the system.", "Had the research instrument validated by experts.", "Obtained informed consent, and parental consent and assent ({{ref:appendix:consent}}).", "Demonstrated the system and let the respondents use it.", "Administered the evaluation questionnaire.", "Retrieved, tallied, and analyzed the responses."]}]
    p["ch3.statistics"] = statistics_blocks(include_difference=True)
    p["ch3.ethics"] = para(LOREM + "Personal data were kept confidential in compliance with the Data Privacy Act of 2012 (Republic Act No. 10173).")

    p["ch4.problems"] = [
        *para("{{ref:table:problems}} maps each problem to a system feature, answering research question {{ref:rq:problems}}."),
        {"type": "table", "caption_id": "problems", "title": "Identified Problems and Corresponding System Features", "columns": [{"label": "Problem Identified"}, {"label": "System Feature / Module That Addresses It"}],
         "rows": [["Lost borrowing records", "Borrowing module"], ["Slow inventory counts", "Inventory module"], ["Manual reports", "Report generation module"]]},
    ]
    p["ch4.design"] = []
    for key, heading, label in (("architecture", "System Architecture", "System Architecture of LabTrack"), ("usecase", "Use Case Diagram / Data Flow Diagram", "Use Case Diagram of LabTrack"), ("erd", "Database Design", "Entity Relationship Diagram of LabTrack"), ("login", "System Modules and User Interface", "Login Page")):
        p["ch4.design"] += [{"type": "heading3", "text": heading}, *para(f"{{{{ref:figure:{key}}}}} shows this part of the system."), {"type": "figure", "caption_id": key, "title": label, "data": image(key.upper()), "width_in": 5.0}]
    p["ch4.testing"] = [
        *para("{{ref:table:tests}} shows sample test cases. All 42 test cases passed after correction."),
        {"type": "table", "caption_id": "tests", "title": "Sample Test Cases and Results", "columns": [{"label": "Test Case ID", "width": 0.8}, {"label": "Module", "width": 0.8}, {"label": "Test Scenario", "width": 1.8}, {"label": "Expected Result", "width": 1.6}, {"label": "Actual Result", "width": 1.6}, {"label": "Status", "width": 0.7, **centered}],
         "rows": [["TC-01", "Login", "User enters a valid username and password", "User is redirected to the dashboard", "User was redirected", "Pass"], ["TC-02", "Login", "User enters a wrong password", "An error message is shown", "Error message shown", "Pass"]]},
    ]
    p["ch4.acceptability"] = [
        *para("{{ref:table:acceptability}} shows that the system was rated Highly Acceptable."),
        {"type": "table", "caption_id": "acceptability", "title": "Summary of the Level of Acceptability of LabTrack", "columns": [{"label": "Characteristic", "width": 2.2}, {"label": "IT Experts WM", **centered}, {"label": "Verbal Interpretation", "width": 1.4, **centered}, {"label": "End-Users WM", **centered}, {"label": "Verbal Interpretation", "width": 1.4, **centered}], "rows": acceptability_rows()},
    ]
    p["ch4.difference"] = [
        {"type": "table", "caption_id": "difference", "title": "Test of Difference Between the Evaluations of IT Experts and End-Users", "columns": [{"label": n, **centered} for n in ("Group", "Mean", "SD", "t-value", "p-value", "Decision on Ho", "Interpretation")],
         "rows": [["IT Experts", "4.50", "0.30", "0.71", "0.48", "Failed to reject", "Not significant"], ["End-Users", "4.41", "0.42", "", "", "", ""]]},
        *para("{{ref:table:difference}} shows no significant difference between the two groups."),
    ]
    p["ch4.enhancements"] = para(LOREM)

    p["ch5.summary"] = [*para("This study developed and evaluated LabTrack."), {"type": "list", "items": [f"Finding for research question {n}." for n in range(1, 7)]}]
    p["ch5.conclusions"] = [{"type": "list", "items": [f"Conclusion {n}." for n in range(1, 7)]}]
    p["ch5.recommendations"] = [{"type": "list", "items": ["The school should adopt the system.", "Users should attend an orientation.", "Future researchers may add a mobile version."]}]

    p["refs.list"] = [
        {"type": "reference", "html": "Dela Cruz, J. P. (2024). Inventory systems in Philippine public schools. <i>Journal of School Technology, 8</i>(2), 11–20. https://doi.org/10.0000/example"},
        {"type": "reference", "html": "Republic of the Philippines. (2012). <i>Data Privacy Act of 2012</i> (Republic Act No. 10173)."},
    ]

    def inserted(title: str) -> list[dict]:
        return [{"type": "figure", "data": image(title, 900, 1200), "width_in": 5.0, "alt": title}]

    p["app.permission"] = inserted("Signed Permission Letter")
    p["app.consent"] = inserted("Consent Form")
    p["app.instrument"] = [
        {"type": "paragraph", "html": "<b>Directions:</b> Please read each statement carefully and put a check (✓) in the column that best describes your evaluation of the system. 5 – Strongly Agree; 4 – Agree; 3 – Neutral; 2 – Disagree; 1 – Strongly Disagree.", "indent": False},
        {"type": "table", "columns": [{"label": "Indicators", "width": 6}, *[{"label": str(n), "width": 0.6, **centered} for n in (5, 4, 3, 2, 1)]],
         "rows": [{"span": "A. Functional Suitability"}, ["1. The system provides all the functions needed to record borrowing.", "", "", "", "", ""], ["2. The system produces correct and accurate results.", "", "", "", "", ""]]},
        {"type": "note", "html": "<b>Comments and Suggestions:</b> ________________________________________"},
    ]
    p["app.validation"] = inserted("Validation Certificates")
    p["app.interview"] = [{"type": "list", "items": ["How is equipment borrowed at present?", "What difficulties do you encounter?"]}]
    p["app.test_cases"] = [{"type": "paragraph", "text": "The complete list of 42 test cases follows.", "indent": False}]
    p["app.gantt"] = [
        {"type": "table", "columns": [{"label": "Activity", "width": 3}, *[{"label": w, "width": 0.7, **centered} for w in GANTT_PERIODS]],
         "rows": [[name, *[{"text": "", "fill": "808080"} if i in weeks else "" for i in range(len(GANTT_PERIODS))]] for name, weeks in GANTT_ACTIVITIES]},
    ]
    p["app.manual"] = [{"type": "list", "items": ["Open the system address in a browser.", "Log in using the account provided."]}]
    p["app.code"] = [{"type": "paragraph", "text": "Repository: https://example.org/labtrack", "indent": False}]
    p["app.documentation"] = [{"type": "figure", "data": image("Evaluation Photo"), "width_in": 4.5, "title": "Respondents during the system evaluation"}]
    p["app.editing"] = inserted("Certificate of Editing")
    p["app.revision"] = [{"type": "table", "columns": [{"label": "No.", "width": 0.5, **centered}, {"label": "Panel Member", "width": 1.4}, {"label": "Comments / Suggestions", "width": 2.4}, {"label": "Action Taken", "width": 2.4}, {"label": "Page No.", "width": 0.7, **centered}], "rows": [["1", "Chairperson", "Clarify the scope.", "Scope was revised.", "9"]]}]
    p["app.cv"] = [{"type": "paragraph", "text": "Curriculum vitae of each proponent.", "indent": False}]
    return p
