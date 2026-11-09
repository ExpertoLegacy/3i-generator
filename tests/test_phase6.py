"""Phase 6 acceptance checks: adviser notes, the AI-use statement, final-report gates, and Defense Prep."""
import json

import pytest
from docx import Document

from app.parts import disclosure
from app.parts import spec as spec_module
from app.parts.export import run_checks
from tests.test_ai import GOOD, LONG_NOTE, fill_background, world  # noqa: F401
from tests.test_app_flow import fill_all

DEFENSE = {
    "slides": [{"title": f"Slide {n}", "bullets": ["A point"], "notes": ""} for n in range(1, 13)],
    "questions": [{"question": "Why did {{PROPONENT_1}} choose Laravel?", "answer_hint": "Chapter 3, Phase 3"}],
}


def blocking(session, key=None):
    return [i for i in run_checks(session) if i["blocking"] and (key is None or i["key"] == key)]


def texts(path):
    return "\n".join(p.text for p in Document(path).paragraphs)


def ready_for_final(client, web):
    """Fill everything and mark every part as reviewed."""
    fill_all(client)
    for key in spec_module.report_keys():
        if web.state.session.draft(key):
            client.post(f"/api/part/{key}/review", json={"reviewed": True})


def review(client, key):
    client.post(f"/api/part/{key}/review", json={"reviewed": True})


# ---- notes ----------------------------------------------------------------------------------


def add_note(client, key="ch1.scope", author="Sir Glodo", body="Please state the school year."):
    return client.post(f"/api/part/{key}/notes", json={"author": author, "body": body}).json()


def test_note_needs_a_typed_name_and_text(world):  # noqa: F811
    client, _web, _relay = world
    assert not add_note(client, author="  ")["ok"]
    assert not add_note(client, body="")["ok"]
    assert add_note(client)["ok"]


def test_note_appears_with_its_author_and_a_count(world):  # noqa: F811
    client, _web, _relay = world
    add_note(client)
    page = client.get("/part/ch1.scope").text
    assert "Please state the school year." in page and "Sir Glodo" in page and "(1 open)" in page and 'class="note-count"' in page


def test_notes_are_escaped(world):  # noqa: F811
    client, _web, _relay = world
    add_note(client, body="<script>alert(1)</script>")
    page = client.get("/part/ch1.scope").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_notes_travel_with_the_session_file(world, tmp_path):  # noqa: F811
    client, web, _relay = world
    add_note(client)
    copy = tmp_path / "copy.rrg"
    web.state.session.copy_to(copy)
    from app.session import Session

    other = Session(copy)
    assert other.notes("ch1.scope")[0]["author"] == "Sir Glodo"
    other.close()


def test_open_note_blocks_final_until_resolved(world):  # noqa: F811
    client, web, _relay = world
    ready_for_final(client, web)
    assert not blocking(web.state.session)
    add_note(client)
    assert any("adviser note" in i["message"] for i in blocking(web.state.session, "ch1.scope"))
    assert not client.post("/api/build", json={"final": True}).json()["ok"]
    assert client.post("/api/build", json={"final": False}).json()["ok"]
    note = web.state.session.notes("ch1.scope")[0]["id"]
    client.post(f"/api/note/{note}", json={"resolved": True})
    assert client.post("/api/build", json={"final": True}).json()["ok"]


def test_notes_are_never_sent_to_ai(world):  # noqa: F811
    client, _web, relay = world
    fill_background(client)
    add_note(client, "ch1.background", body="SECRET ADVISER COMMENT")
    client.post("/api/part/ch1.background/ai/write")
    assert "SECRET ADVISER COMMENT" not in relay.sent_text()


# ---- the AI-use statement -------------------------------------------------------------------


def test_no_statement_without_ai_use(world):  # noqa: F811
    client, web, _relay = world
    ready_for_final(client, web)
    assert disclosure.statement(web.state.session) == ""
    assert "no AI use so far" in client.get("/part/ch3.ethics").text
    assert client.post("/api/build", json={"final": True}).json()["ok"]
    assert "generative artificial intelligence" not in texts(web.state.last_export)


def ai_drafted(client):
    fill_background(client)
    client.post("/api/part/ch1.background/ai/write")


def test_statement_reports_exactly_what_the_log_shows(world):  # noqa: F811
    client, web, _relay = world
    ai_drafted(client)
    text = disclosure.statement(web.state.session)
    assert "test-model" in text and "Cloudflare Workers AI" in text
    assert "draft text for Chapter 1 (Background)" in text
    assert "Chapter 2" not in text and "suggest ideas" not in text and "concept mockup" not in text and "edited" not in text


def test_statement_grows_with_each_kind_of_use(world):  # noqa: F811
    client, web, relay = world
    ai_drafted(client)
    relay.replies.append({"suggestions": ["A limit"]})
    client.post("/api/part/ch1.scope/ai/suggest", json={"field": "limits"})
    client.post("/api/part/ch1.background/draft", json={"html": "<p>Rewritten by the group.</p>"})
    text = disclosure.statement(web.state.session)
    assert "suggest ideas for Chapter 1 (Scope and Limitations)" in text and "edited 1 of the 1 AI-drafted parts" in text


def test_failed_generations_do_not_count(world):  # noqa: F811
    client, web, relay = world
    fill_background(client)
    relay.replies += ["bad", "still bad"]
    client.post("/api/part/ch1.background/ai/write")
    assert disclosure.statement(web.state.session) == ""


def test_logo_and_defense_are_not_part_of_the_paper(world):  # noqa: F811
    client, web, _relay = world
    client.post("/api/part/setup.system/ai/image", json={"description": "a flask with a checkmark"})
    assert disclosure.statement(web.state.session) == ""


def test_kept_mockup_is_disclosed_but_an_unkept_one_is_not(world):  # noqa: F811
    client, web, _relay = world
    made = client.post("/api/part/ch3.method.phase2/ai/image", json={"description": "a borrowing form with a list"}).json()
    assert disclosure.statement(web.state.session) == ""
    client.post(f"/api/attachment/{made['attachment']}/keep")
    assert "1 concept mockup, each labeled" in disclosure.statement(web.state.session)


def test_statement_reaches_word_in_ethics_and_development(world):  # noqa: F811
    client, web, _relay = world
    fill_all(client)
    client.post("/api/part/ch1.background/inputs", json={"context": LONG_NOTE, "situation": LONG_NOTE, "problems": LONG_NOTE})
    client.post("/api/part/ch1.background/ai/write")
    client.post("/api/build", json={"final": False})
    text = texts(web.state.last_export)
    assert disclosure.statement(web.state.session) in text
    assert disclosure.DEVELOPMENT_SENTENCE in text
    assert text.index("Ethical Considerations") < text.index(disclosure.statement(web.state.session))


def test_unconfirmed_statement_blocks_final(world):  # noqa: F811
    client, web, _relay = world
    ready_for_final(client, web)
    client.post("/api/part/ch1.background/inputs", json={"context": LONG_NOTE, "situation": LONG_NOTE, "problems": LONG_NOTE})
    client.post("/api/part/ch1.background/ai/write")
    review(client, "ch1.background")
    assert [i["key"] for i in blocking(web.state.session)] == ["ch3.ethics"]
    assert not client.post("/api/build", json={"final": True}).json()["ok"]
    client.post("/api/disclosure", json={"confirmed": True})
    assert client.post("/api/build", json={"final": True}).json()["ok"]


def test_new_ai_use_cancels_the_confirmation(world):  # noqa: F811
    client, web, relay = world
    ai_drafted(client)
    client.post("/api/disclosure", json={"confirmed": True})
    assert disclosure.is_confirmed(web.state.session)
    relay.replies.append({"suggestions": ["A limit"]})
    client.post("/api/part/ch1.scope/ai/suggest", json={"field": "limits"})
    assert not disclosure.is_confirmed(web.state.session)
    assert "checked" not in client.get("/part/ch3.ethics").text.split('id="disclosure-confirm"')[1].split(">")[0]


def test_ethics_page_shows_the_real_log(world):  # noqa: F811
    client, _web, _relay = world
    ai_drafted(client)
    page = client.get("/part/ch3.ethics").text
    assert "See the log (1 use)" in page and "Drafted text" in page and "test-model" in page


# ---- other final conditions -----------------------------------------------------------------


def test_invented_citation_blocks_final(world):  # noqa: F811
    client, web, _relay = world
    ready_for_final(client, web)
    client.post("/api/part/ch1.scope/draft", json={"html": "<p>Studies agree (Ghost, 2020).</p>"})
    review(client, "ch1.scope")
    assert any("Ghost" in i["message"] for i in blocking(web.state.session, "ch1.scope"))


def test_stale_diagram_blocks_final(world):  # noqa: F811
    client, web, _relay = world
    spec = {"nodes": [{"id": "a", "label": "users"}, {"id": "b", "label": "items"}], "edges": [{"from": "a", "to": "b", "label": "1:N"}]}
    client.post("/api/part/ch4.design.database/diagram/draw", json={"kind": "erd", "spec": spec})
    spec["nodes"][0]["label"] = "accounts"
    client.post("/api/part/ch4.design.database/diagram", json={"kind": "erd", "spec": spec})
    assert any("Draw it again" in i["message"] for i in blocking(web.state.session, "ch4.design.database"))


def test_scope_missing_a_module_is_flagged_without_blocking(world):  # noqa: F811
    client, web, _relay = world
    client.post("/api/part/setup.system/inputs", json={"system_name": "LabTrack", "modules": "Equipment Inventory\nReports"})
    client.post("/api/part/ch1.scope/draft", json={"html": "<p>x</p>"})
    client.post("/api/part/ch1.scope/generate")
    client.post("/api/part/ch1.scope/draft", json={"html": "<p>The system covers the equipment inventory.</p>"})
    found = [i for i in run_checks(web.state.session) if "does not mention" in i["message"]]
    assert len(found) == 1 and "Reports" in found[0]["message"] and "Equipment" not in found[0]["message"] and not found[0]["blocking"]


def test_framework_and_model_mismatch_is_flagged(world):  # noqa: F811
    client, web, _relay = world
    client.post("/api/part/ch1.framework/diagram", json={"kind": "ipo", "spec": {"nodes": [{"id": "a", "label": "Planning", "type": "Process"}, {"id": "b", "label": "Coding", "type": "Process"}]}})
    client.post("/api/part/ch3.method.overview/diagram", json={"kind": "model", "spec": {"nodes": [{"id": "a", "label": "Planning"}, {"id": "b", "label": "Testing"}]}})
    assert any("do not match the phases" in i["message"] for i in run_checks(web.state.session))
    client.post("/api/part/ch3.method.overview/diagram", json={"kind": "model", "spec": {"nodes": [{"id": "a", "label": "Planning"}, {"id": "b", "label": "Coding and Unit Tests"}]}})
    assert not any("do not match the phases" in i["message"] for i in run_checks(web.state.session))


def test_final_has_no_draft_marks_and_draft_does(world):  # noqa: F811
    client, web, _relay = world
    ready_for_final(client, web)
    client.post("/api/build", json={"final": False})
    assert "DRAFT" in "".join(p.text for s in Document(web.state.last_export).sections for p in s.header.paragraphs)
    assert client.post("/api/build", json={"final": True}).json()["ok"]
    assert "DRAFT" not in "".join(p.text for s in Document(web.state.last_export).sections for p in s.header.paragraphs)


def test_final_is_refused_by_the_server_even_if_the_button_is_bypassed(world):  # noqa: F811
    client, web, _relay = world
    fill_all(client)
    client.post("/api/part/ch1.scope/review", json={"reviewed": False})
    web.state.last_export = None
    assert not client.post("/api/build", json={"final": True}).json()["ok"]
    assert web.state.last_export is None


# ---- Defense Prep ---------------------------------------------------------------------------


def test_defense_fill_uses_only_the_report(world):  # noqa: F811
    client, web, relay = world
    fill_all(client)
    scores = client.get("/part/ch4.acceptability")  # the page must render with data
    assert scores.status_code == 200
    assert client.post("/api/defense/fill").json()["ok"]
    values = web.state.session.inputs("finish.defense")
    assert 10 <= len(values["slides"]) <= 15
    titles = [s["title"] for s in values["slides"]]
    for needed in ("Objectives", "Conceptual Framework", "Methodology", "Live System Demonstration", "Results: Level of Acceptability", "Conclusions", "Recommendations"):
        assert needed in titles
    results = next(s for s in values["slides"] if s["title"].startswith("Results"))
    assert "overall weighted mean 4.50 (Highly Acceptable)" in results["bullets"]
    assert len(values["questions"]) >= 10
    assert not relay.text_bodies()


def test_defense_page_saves_and_downloads_word(world):  # noqa: F811
    client, web, _relay = world
    client.post("/api/defense/fill")
    assert "Fill from my report" in client.get("/part/finish.defense").text
    values = web.state.session.inputs("finish.defense")
    values["questions"][0]["answer"] = "Our own answer here."
    client.post("/api/part/finish.defense/inputs", json=values)
    assert client.post("/api/defense/download").json()["ok"]
    text = texts(web.state.last_export)
    assert "Slide outline" in text and "Our own answer here." in text


def test_defense_is_not_in_the_report(world):  # noqa: F811
    client, web, _relay = world
    assert "finish.defense" not in spec_module.report_keys()
    fill_all(client)
    client.post("/api/defense/fill")
    assert not [i for i in run_checks(web.state.session) if i["key"] == "finish.defense"]


def test_defense_ai_is_refused_without_results(world):  # noqa: F811
    client, _web, relay = world
    result = client.post("/api/part/finish.defense/ai/defense").json()
    assert not result["ok"] and "Build this part first" in result["message"]
    assert not relay.text_bodies()


def test_defense_ai_restores_names_flags_inventions_and_stays_out_of_the_paper_statement(world):  # noqa: F811
    client, web, relay = world
    fill_all(client)
    client.post("/api/part/setup.people/inputs", json={"proponents": [{"name": "Maria Clara Dela Cruz"}]})
    reply = json.loads(json.dumps(DEFENSE))
    reply["slides"][0]["bullets"] = ["About 91 percent were satisfied (Ghost, 2019)"]
    relay.replies.append(reply)
    assert client.post("/api/part/finish.defense/ai/defense").json()["issues"] >= 2
    values = web.state.session.inputs("finish.defense")
    assert len(values["slides"]) == 12
    assert "Maria Clara Dela Cruz" in values["questions"][0]["question"] and values["questions"][0]["answer"].startswith("Hint:")
    page = client.get("/part/finish.defense").text
    assert "Ghost" in page and "91" in page
    assert "Dela Cruz" not in relay.sent_text()
    assert disclosure.statement(web.state.session) == ""


def test_too_few_slides_is_rejected_and_keeps_student_work(world):  # noqa: F811
    client, web, relay = world
    fill_all(client)
    client.post("/api/defense/fill")
    before = web.state.session.inputs("finish.defense")
    relay.replies += [{"slides": [], "questions": []}, {"slides": [], "questions": []}]
    assert not client.post("/api/part/finish.defense/ai/defense").json()["ok"]
    assert web.state.session.inputs("finish.defense") == before


def test_defense_background_cannot_enter_the_report(world):  # noqa: F811
    client, _web, _relay = world
    made = client.post("/api/part/finish.defense/ai/image", json={"description": "soft blue shapes for science"}).json()
    assert made["ok"] and not client.post(f"/api/attachment/{made['attachment']}/keep").json()["ok"]


@pytest.mark.parametrize("key", ["setup.school", "finish.check", "finish.build", "finish.checklist"])
def test_notes_do_not_appear_on_setup_or_finish_views(world, key):  # noqa: F811
    client, _web, _relay = world
    assert 'class="notes"' not in client.get(f"/part/{key}").text
