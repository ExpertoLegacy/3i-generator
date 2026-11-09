"""The guided path: approve each part in order, skip what is optional, Chapter 2 builds on Chapter 1."""
import pytest
from docx import Document

from tests.test_ai import world  # noqa: F401, I001 - first, because it puts the app in test mode
from tests.test_guided import FIRST, SETUP  # noqa: E402

from app.ai_client import service  # noqa: E402
from app.parts import generators as g  # noqa: E402
from app.parts import spec as spec_module  # noqa: E402
from app.parts.export import run_checks  # noqa: E402

pytestmark = pytest.mark.gated


def built_and_approved(client, key, html="<p>Approved text for this part.</p>"):
    client.post(f"/api/part/{key}/generate")
    client.post(f"/api/part/{key}/draft", json={"html": html})
    return client.post(f"/api/part/{key}/review", json={"reviewed": True}).json()


def finish_chapter_1(client):
    for key in ("ch1.background", "ch1.sop", "ch1.objectives", "ch1.hypothesis", "ch1.framework", "ch1.scope", "ch1.significance", "ch1.terms"):
        built_and_approved(client, key)


def test_only_the_first_part_is_open_at_the_start(world):  # noqa: F811
    client, web, _relay = world
    locks = g.locks(web.state.session)
    assert "ch1.background" not in locks and locks["ch1.sop"] == "ch1.background" and locks["ch5.summary"] == "ch1.background"


def test_a_locked_page_shows_no_work_area_and_points_to_the_open_part(world):  # noqa: F811
    client, _web, _relay = world
    page = client.get("/part/ch1.sop").text
    assert "locked-panel" in page and "/part/ch1.background" in page and 'id="inputs"' not in page
    assert 'id="inputs"' in client.get("/part/ch1.background").text


def test_approving_opens_only_the_next_part(world):  # noqa: F811
    client, web, _relay = world
    assert built_and_approved(client, "ch1.background")["ok"]
    locks = g.locks(web.state.session)
    assert "ch1.sop" not in locks and locks["ch1.objectives"] == "ch1.sop"


def test_a_draft_alone_does_not_open_the_next_part(world):  # noqa: F811
    client, web, _relay = world
    client.post("/api/part/ch1.background/generate")
    assert g.locks(web.state.session)["ch1.sop"] == "ch1.background"


def test_chapter_2_opens_only_after_all_of_chapter_1(world):  # noqa: F811
    client, web, _relay = world
    finish_chapter_1(client)
    assert "ch2.literature" not in g.locks(web.state.session)
    assert g.locks(web.state.session)["ch2.studies"] == "ch2.literature"


def test_one_unapproved_part_keeps_chapter_2_closed(world):  # noqa: F811
    client, web, _relay = world
    for key in ("ch1.background", "ch1.sop", "ch1.objectives", "ch1.hypothesis", "ch1.framework", "ch1.scope", "ch1.significance"):
        built_and_approved(client, key)
    assert g.locks(web.state.session)["ch2.literature"] == "ch1.terms"
    tabs = {t["id"]: t for t in web.tab_view(web.state.session)}
    assert tabs["ch2"]["locked"] and not tabs["ch1"]["locked"]


def test_source_library_is_always_open(world):  # noqa: F811
    client, web, _relay = world
    assert "ch2.sources" not in g.locks(web.state.session)
    assert 'id="inputs"' in client.get("/part/ch2.sources").text


def test_setup_preliminaries_and_finish_are_not_locked(world):  # noqa: F811
    client, web, _relay = world
    locks = g.locks(web.state.session)
    assert not [k for k in locks if k.split(".")[0] in ("setup", "prelim", "refs", "app", "finish")]


def test_editing_an_approved_part_needs_approval_again_but_keeps_later_work_open(world):  # noqa: F811
    client, web, _relay = world
    built_and_approved(client, "ch1.background")
    built_and_approved(client, "ch1.sop")
    client.post("/api/part/ch1.background/draft", json={"html": "<p>Revised after the panel.</p>"})
    assert web.state.session.status()["ch1.background"] == "draft"
    assert "ch1.sop" not in g.locks(web.state.session)
    assert g.locks(web.state.session)["ch1.objectives"] == "ch1.background"
    assert any(i["key"] == "ch1.background" and i["blocking"] for i in run_checks(web.state.session))


def test_gaps_cannot_be_approved(world):  # noqa: F811
    client, web, _relay = world
    client.post("/api/part/ch1.background/generate")
    client.post("/api/part/ch1.background/draft", json={"html": "<p>About [KULANG: count] per week.</p>"})
    result = client.post("/api/part/ch1.background/review", json={"reviewed": True}).json()
    assert not result["ok"] and "KULANG" in result["message"] and web.state.session.status()["ch1.background"] != "reviewed"


# ---- optional parts ----------------------------------------------------------------------------


def test_only_optional_parts_can_be_skipped(world):  # noqa: F811
    client, _web, _relay = world
    assert not client.post("/api/part/ch1.background/skip", json={"skip": True}).json()["ok"]
    assert "Not necessary" not in client.get("/part/ch1.background").text


def test_not_necessary_opens_the_next_part_and_can_be_undone(world):  # noqa: F811
    client, web, _relay = world
    built_and_approved(client, "ch1.background")
    built_and_approved(client, "ch1.sop")
    built_and_approved(client, "ch1.objectives")
    assert "Not necessary" in client.get("/part/ch1.hypothesis").text
    assert client.post("/api/part/ch1.hypothesis/skip", json={"skip": True}).json()["ok"]
    assert "ch1.framework" not in g.locks(web.state.session)
    assert "Needed after all" in client.get("/part/ch1.hypothesis").text
    tabs = {t["id"]: t for t in web.tab_view(web.state.session)}
    assert next(p for p in tabs["ch1"]["parts"] if p["key"] == "ch1.hypothesis")["status"] == "skipped"
    client.post("/api/part/ch1.hypothesis/skip", json={"skip": False})
    assert g.locks(web.state.session)["ch1.framework"] == "ch1.hypothesis"


def test_skipping_hypothesis_also_drops_the_difference_part_and_question(world):  # noqa: F811
    from tests.test_app_flow import fill_all

    client, web, _relay = world
    fill_all(client)
    before = g.research_question_ids(web.state.session)
    client.post("/api/part/ch1.hypothesis/skip", json={"skip": True})
    session = web.state.session
    assert "difference" in before and "difference" not in g.research_question_ids(session) and "ch4.difference" in g.disabled_parts(session)


def test_skipped_appendix_is_left_out_of_word(world):  # noqa: F811
    from tests.test_app_flow import fill_all

    client, web, _relay = world
    fill_all(client)
    client.post("/api/part/app.editing/skip", json={"skip": True})
    assert client.post("/api/build", json={"final": False}).json()["ok"]
    assert "Certificate of Editing" not in "\n".join(p.text for p in Document(web.state.last_export).paragraphs)


# ---- guided drafts on every part ---------------------------------------------------------------


@pytest.mark.parametrize("key", ["ch1.background", "ch1.objectives", "ch1.framework", "ch1.scope", "ch1.significance", "ch1.terms"])
def test_chapters_1_and_2_have_guided_drafts(key):
    config = spec_module.ai_config(key)
    assert "guided" in config["modes"] and config["guided"]["setup"]
    assert {f["id"] for f in spec_module.part(key)["inputs"]} >= {"answers", "search_terms"}


TOPICS = {"topics": [{"topic": "Inventory management", "search": "school inventory management"}, {"topic": "Web-based school systems", "search": "web based school information system"}]}
DRAFT = {"blocks": [{"type": "heading3", "text": "Inventory management"}, {"type": "paragraph", "text": "Web systems reduce errors (Cruz0, 2023)."}], "citations_used": ["Cruz0, 2023"], "missing_info": []}


def study(n, word="Study"):
    return {"authors": f"Cruz{n}, A.", "year": "2023", "title": f"{word} {n}", "container": "J", "volume": "", "issue": "", "pages": "", "link": f"https://doi.org/10.1/{n}", "type": "Journal article", "origin": "Foreign", "notes": f"Abstract number {n}."}


@pytest.fixture()
def searches(monkeypatch):
    asked = []

    def search(query, client=None):
        asked.append(query)
        offset = len(asked) * 10
        return [study(offset + n) for n in range(4)]

    monkeypatch.setattr("app.sources.apa.search_studies", search)
    return asked


def recommend(client, relay, key="ch2.literature"):
    client.post("/api/part/setup.system/inputs", json=SETUP)
    relay.replies.append(TOPICS)
    return client.post(f"/api/part/{key}/ai/topics").json()


def test_topics_are_recommended_from_the_approved_chapter_1(world):  # noqa: F811
    client, web, relay = world
    finish_chapter_1(client)
    assert recommend(client, relay)["ok"]
    body = relay.text_bodies()[-1]
    assert body["mode"] == "topics" and "Approved text" in body["masked_input"]["context"]["Background"]
    assert [r["topic"] for r in web.state.session.inputs("ch2.literature")["research_topics"]] == ["Inventory management", "Web-based school systems"]


def test_nothing_is_searched_until_the_topics_are_approved(world, searches):  # noqa: F811
    client, web, relay = world
    recommend(client, relay)
    assert not searches and not web.state.session.inputs("ch2.sources")


def test_approving_topics_searches_fills_the_library_and_drafts(world, searches):  # noqa: F811
    client, web, relay = world
    recommend(client, relay)
    relay.replies.append(DRAFT)
    result = client.post("/api/part/ch2.literature/ai/research").json()
    assert result["ok"] and result["sources"] == 8 and searches == ["school inventory management", "web based school information system"]
    library = web.state.session.inputs("ch2.sources")["sources"]
    assert len(library) == 8 and all(s["used_for"] == "ch2.literature" for s in library)
    sent = relay.text_bodies()[-1]["masked_input"]["sources"]
    assert len(sent) == 8 and sent[0]["notes"].startswith("Abstract number")
    draft = web.state.session.draft("ch2.literature")
    assert "Inventory management" in draft["html"] and web.state.session.status()["ch2.literature"] == "draft"
    page = client.get("/part/ch2.literature").text
    assert "Alisin" in page and "https://doi.org/10.1/" in page and "Study 10" in page


def test_student_edited_topics_are_what_gets_searched(world, searches):  # noqa: F811
    client, web, relay = world
    recommend(client, relay)
    client.post("/api/part/ch2.literature/inputs", json={"research_topics": [{"topic": "Our own topic", "search": "our own search words"}]})
    relay.replies.append(DRAFT)
    client.post("/api/part/ch2.literature/ai/research")
    assert searches == ["our own search words"]


def test_each_part_uses_only_its_own_studies(world, searches):  # noqa: F811
    client, web, relay = world
    recommend(client, relay)
    relay.replies.append(DRAFT)
    client.post("/api/part/ch2.literature/ai/research")
    relay.replies.append(TOPICS)
    client.post("/api/part/ch2.systems/ai/topics")
    relay.replies.append({**DRAFT, "blocks": [{"type": "paragraph", "lead": "A system", "text": "It tracks items."}], "citations_used": []})
    client.post("/api/part/ch2.systems/ai/research")
    titles = {s["title"] for s in relay.text_bodies()[-1]["masked_input"]["sources"]}
    assert titles == {f"Study {n}" for n in (30, 31, 32, 40, 41, 42)}


def test_studies_also_search_for_local_research(world, searches):  # noqa: F811
    client, _web, relay = world
    recommend(client, relay, "ch2.studies")
    relay.replies.append(DRAFT)
    client.post("/api/part/ch2.studies/ai/research")
    assert "school inventory management Philippines" in searches and "school inventory management" in searches


def test_failed_search_changes_nothing(world, monkeypatch):  # noqa: F811
    from app.sources import apa

    client, web, relay = world
    recommend(client, relay)

    def down(_query):
        raise apa.LookupError_("offline")

    monkeypatch.setattr("app.sources.apa.search_studies", down)
    assert not client.post("/api/part/ch2.literature/ai/research").json()["ok"]
    assert web.state.session.draft("ch2.literature") is None and not web.state.session.inputs("ch2.sources")


def test_studies_without_abstracts_are_not_used(world, monkeypatch):  # noqa: F811
    client, web, relay = world
    recommend(client, relay)
    monkeypatch.setattr("app.sources.apa.search_studies", lambda _q: [{**study(n), "notes": ""} for n in range(6)])
    result = client.post("/api/part/ch2.literature/ai/research").json()
    assert not result["ok"] and "search words" in result["message"] and web.state.session.draft("ch2.literature") is None


def test_an_invented_citation_in_the_research_draft_is_flagged(world, searches):  # noqa: F811
    client, web, relay = world
    recommend(client, relay)
    relay.replies.append({**DRAFT, "blocks": [{"type": "paragraph", "text": "Claims hold (Ghost, 2019)."}]})
    client.post("/api/part/ch2.literature/ai/research")
    assert any(i["rule"] == "V1" for i in web.state.session.draft("ch2.literature")["ai"]["issues"])


def test_a_removed_source_blocks_the_final_report_if_still_cited(world, searches):  # noqa: F811
    client, web, relay = world
    recommend(client, relay)
    relay.replies.append({**DRAFT, "blocks": [{"type": "paragraph", "text": "Web systems reduce errors (Cruz10, 2023)."}], "citations_used": []})
    client.post("/api/part/ch2.literature/ai/research")
    assert client.post("/api/sources/remove", json={"title": "Study 10"}).json()["ok"]
    assert any("Cruz10" in i["message"] and i["blocking"] for i in run_checks(web.state.session) if i["key"] == "ch2.literature")


def test_approving_the_draft_updates_progress_and_reaches_the_report(world, searches):  # noqa: F811
    from tests.test_app_flow import fill_all

    client, web, relay = world
    fill_all(client)
    recommend(client, relay)
    relay.replies.append({**DRAFT, "blocks": [{"type": "paragraph", "text": "A researched paragraph about inventory (Cruz10, 2023)."}], "citations_used": []})
    client.post("/api/part/ch2.literature/ai/research")
    assert client.post("/api/part/ch2.literature/review", json={"reviewed": True}).json()["ok"]
    assert web.state.session.status()["ch2.literature"] == "reviewed"
    client.post("/api/build", json={"final": False})
    assert "A researched paragraph about inventory" in "\n".join(p.text for p in Document(web.state.last_export).paragraphs)


SYNTHESIS = {
    "system_names": ["InvenTrack", "LabManager", "EduStock"],
    "features": [{"feature": "Borrowing records", "a": "Yes", "b": "unknown", "c": "No", "own": "Yes"}, {"feature": "SMS alerts", "a": "Not stated", "b": "Not stated", "c": "Not stated", "own": "No"}],
    "blocks": [{"type": "paragraph", "text": "Taken together, the reviewed works show a gap at the locale, as {{ref:table:features}} shows."}],
    "citations_used": [], "missing_info": [],
}


def approve_chapter_2_review_parts(client):
    for key in ("ch2.literature", "ch2.studies", "ch2.systems"):
        built_and_approved(client, key, f"<p>Approved {key} text.</p>")


def test_synthesis_waits_for_the_three_review_parts(world):  # noqa: F811
    client, _web, relay = world
    client.post("/api/part/setup.system/inputs", json=SETUP)
    result = client.post("/api/part/ch2.synthesis/ai/synthesize").json()
    assert not result["ok"] and "Related Literature" in result["message"] and not relay.text_bodies()


def test_synthesis_is_written_from_the_approved_parts_with_no_student_answers(world):  # noqa: F811
    client, web, relay = world
    client.post("/api/part/setup.system/inputs", json=SETUP)
    approve_chapter_2_review_parts(client)
    relay.replies.append(SYNTHESIS)
    assert client.post("/api/part/ch2.synthesis/ai/synthesize").json()["ok"]
    body = relay.text_bodies()[-1]
    assert body["mode"] == "synthesis" and "Approved ch2.systems text" in body["masked_input"]["context"]["Related Systems"]
    draft = web.state.session.draft("ch2.synthesis")
    assert "gap at the locale" in draft["html"] and draft["after"][0]["caption_id"] == "features"
    assert web.state.session.status()["ch2.synthesis"] == "draft"


def test_uncertain_feature_claims_become_not_stated(world):  # noqa: F811
    client, web, relay = world
    client.post("/api/part/setup.system/inputs", json=SETUP)
    approve_chapter_2_review_parts(client)
    relay.replies.append(SYNTHESIS)
    client.post("/api/part/ch2.synthesis/ai/synthesize")
    row = web.state.session.inputs("ch2.synthesis")["matrix"][0]
    assert (row["a"], row["b"], row["c"], row["own"]) == ("Yes", "Not stated", "Not stated", "Yes")


def test_student_can_correct_the_table_without_losing_edited_text(world):  # noqa: F811
    client, web, relay = world
    client.post("/api/part/setup.system/inputs", json=SETUP)
    approve_chapter_2_review_parts(client)
    relay.replies.append(SYNTHESIS)
    client.post("/api/part/ch2.synthesis/ai/synthesize")
    client.post("/api/part/ch2.synthesis/draft", json={"html": "<p>Our edited discussion.</p>"})
    values = web.state.session.inputs("ch2.synthesis")
    values["matrix"][0]["b"] = "No"
    client.post("/api/part/ch2.synthesis/inputs", json=values)
    assert client.post("/api/part/ch2.synthesis/refresh-tables").json()["ok"]
    draft = web.state.session.draft("ch2.synthesis")
    assert draft["html"] == "<p>Our edited discussion.</p>" and draft["after"][0]["rows"][0][2] == "No"


def test_approving_synthesis_finishes_chapter_2_and_opens_chapter_3(world):  # noqa: F811
    client, web, relay = world
    client.post("/api/part/setup.system/inputs", json=SETUP)
    finish_chapter_1(client)
    approve_chapter_2_review_parts(client)
    assert g.locks(web.state.session)["ch3.design"] == "ch2.synthesis"
    relay.replies.append(SYNTHESIS)
    client.post("/api/part/ch2.synthesis/ai/synthesize")
    assert g.locks(web.state.session)["ch3.design"] == "ch2.synthesis"
    client.post("/api/part/ch2.synthesis/review", json={"reviewed": True})
    assert "ch3.design" not in g.locks(web.state.session)


def test_questions_about_sources_are_not_shown_to_students(world):  # noqa: F811
    client, web, relay = world
    client.post("/api/part/setup.system/inputs", json=SETUP)
    questions = [*FIRST["questions"], {"section": "Context", "question_en": "Can you provide studies on inventory systems?", "question_tl": "May pag-aaral ka ba?"}]
    relay.replies.append({**FIRST, "questions": questions})
    client.post("/api/part/ch1.background/ai/guided")
    assert not [r for r in web.state.session.inputs("ch1.background")["answers"] if "studies" in r["question_en"]]


def test_a_part_with_few_questions_needs_only_those_answered(world):  # noqa: F811
    client, web, relay = world
    client.post("/api/part/setup.system/inputs", json=SETUP)
    relay.replies.append({**FIRST, "questions": FIRST["questions"][:2]})
    client.post("/api/part/ch1.objectives/ai/guided")
    client.post("/api/part/ch1.sop/generate")
    values = web.state.session.inputs("ch1.objectives")
    for row in values["answers"]:
        row["answer"] = "Sagot."
    assert client.post("/api/part/ch1.objectives/inputs", json=values).json()["ai_blocked"] == []
    assert service.GUIDED_MINIMUM == 3
