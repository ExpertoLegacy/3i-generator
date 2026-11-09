"""The guided Background: first draft from setup, targeted questions, real sources, Tagalog answers."""
import httpx
import pytest

from tests.test_ai import world  # noqa: F401, I001 - first, because it puts the app in test mode

from app.ai_client.masking import Masker  # noqa: E402
from app.ai_client.validators import tagalog_numbers  # noqa: E402
from app.parts.export import run_checks  # noqa: E402
from app.sources import apa  # noqa: E402

SETUP = {"system_name": "LabTrack", "platform": "web-based", "system_type": "laboratory inventory system", "users": "the science department", "locale": "Cabiao Senior High School", "process": "equipment borrowing process", "dev_model": "Agile (Scrum)", "modules": "Inventory\nReports"}
FIRST = {
    "blocks": [{"type": "paragraph", "text": "The science department handles the equipment borrowing process manually, with about [KULANG: weekly transactions] each week."}],
    "questions": [{"section": "Current situation", "question_en": f"Question {n} in English?", "question_tl": f"Tanong {n} sa Tagalog?"} for n in range(1, 7)],
    "search_terms": ["laboratory inventory system", "school inventory Philippines"],
}
FINAL = {"blocks": [{"type": "paragraph", "text": "The department records about 40 borrowing transactions each week."}], "citations_used": [], "missing_info": []}


def setup(client):
    client.post("/api/part/setup.system/inputs", json=SETUP)


def first_draft(client, relay):
    setup(client)
    relay.replies.append(FIRST)
    return client.post("/api/part/ch1.background/ai/guided").json()


def answer(client, web, count, text="Bawat linggo, mga apatnapu ang humihiram."):
    values = web.state.session.inputs("ch1.background")
    for row in values["answers"][:count]:
        row["answer"] = text
    return client.post("/api/part/ch1.background/inputs", json=values).json()


def test_first_draft_needs_project_setup_not_student_notes(world):  # noqa: F811
    client, web, relay = world
    client.post("/api/part/setup.system/inputs", json={"system_name": "LabTrack"})
    result = client.post("/api/part/ch1.background/ai/guided").json()
    assert not result["ok"] and "Project Setup" in result["message"]
    assert not relay.text_bodies()
    assert first_draft(client, relay)["ok"]
    assert "[KULANG: weekly transactions]" in web.state.session.draft("ch1.background")["html"]


def test_first_draft_sends_setup_and_saves_bilingual_questions(world):  # noqa: F811
    client, web, relay = world
    first_draft(client, relay)
    assert relay.text_bodies()[0]["mode"] == "guided" and relay.text_bodies()[0]["masked_input"]["facts"]["system_name"] == "LabTrack"
    values = web.state.session.inputs("ch1.background")
    assert len(values["answers"]) == 6 and values["search_terms"].startswith("laboratory")
    page = client.get("/part/ch1.background").text
    assert "Tanong 1 sa Tagalog?" in page and "Question 1 in English?" in page and "Sumagot sa Tagalog, Taglish, o English" in page


def test_first_draft_marks_gaps_without_blocking_student_review(world):  # noqa: F811
    client, web, relay = world
    first_draft(client, relay)
    assert web.state.session.draft("ch1.background")["ai"]["issues"] == []


def test_final_report_stays_blocked_while_gaps_remain(world):  # noqa: F811
    client, web, relay = world
    first_draft(client, relay)
    client.post("/api/part/ch1.background/review", json={"reviewed": True})
    assert any("bracketed" in i["message"] and i["blocking"] for i in run_checks(web.state.session) if i["key"] == "ch1.background")


def test_final_writing_waits_for_real_answers(world):  # noqa: F811
    client, _web, relay = world
    setup(client)
    assert "Hakbang 1" in client.post("/api/part/ch1.background/ai/write").json()["message"]
    first_draft(client, relay)
    calls = len(relay.text_bodies())
    assert "Hakbang 2" in client.post("/api/part/ch1.background/ai/write").json()["message"]
    assert len(relay.text_bodies()) == calls


def test_saving_answers_unlocks_writing_as_soon_as_enough_are_given(world):  # noqa: F811
    client, web, relay = world
    first_draft(client, relay)
    assert answer(client, web, 2)["ai_blocked"]
    assert answer(client, web, 3)["ai_blocked"] == []


def test_tagalog_answers_are_sent_and_their_numbers_count(world):  # noqa: F811
    client, web, relay = world
    first_draft(client, relay)
    answer(client, web, 3)
    relay.replies.append(FINAL)
    assert client.post("/api/part/ch1.background/ai/write").json()["ok"]
    assert "apatnapu" in relay.text_bodies()[-1]["masked_input"]["inputs"]["answers"][0]["answer"]
    draft = web.state.session.draft("ch1.background")
    assert "about 40 borrowing" in draft["html"] and not [i for i in draft["ai"]["issues"] if i["rule"] == "V2"]


def test_an_invented_number_is_still_flagged_after_tagalog_answers(world):  # noqa: F811
    client, web, relay = world
    first_draft(client, relay)
    answer(client, web, 3)
    relay.replies.append({"blocks": [{"type": "paragraph", "text": "The department records about 85 transactions."}]})
    client.post("/api/part/ch1.background/ai/write")
    assert any(i["rule"] == "V2" and "85" in i["message"] for i in web.state.session.draft("ch1.background")["ai"]["issues"])


def test_asking_again_keeps_answers_already_given(world):  # noqa: F811
    client, web, relay = world
    first_draft(client, relay)
    answer(client, web, 2, "Tatlong araw.")
    relay.replies.append({**FIRST, "questions": [{"section": "Gap", "question_en": "A new question?", "question_tl": "Bagong tanong?"}]})
    client.post("/api/part/ch1.background/ai/guided")
    rows = web.state.session.inputs("ch1.background")["answers"]
    assert [r["answer"] for r in rows[:2]] == ["Tatlong araw.", "Tatlong araw."] and rows[-1]["question_en"] == "A new question?"


def test_first_draft_failure_changes_nothing(world):  # noqa: F811
    client, web, relay = world
    setup(client)
    relay.fail_next = httpx.ReadError("dropped")
    assert not client.post("/api/part/ch1.background/ai/guided").json()["ok"]
    assert web.state.session.draft("ch1.background") is None


def test_no_internet_path_is_still_there(world, monkeypatch):  # noqa: F811
    client, _web, _relay = world
    assert "Walang internet? Sagutin dito." in client.get("/part/ch1.background").text
    monkeypatch.setenv("RRG_RELAY_URL", "")
    page = client.get("/part/ch1.background").text
    assert "Create draft" in page and "Hakbang 1" not in page and "Ano ang iminumungkahing sistema" in page


def test_other_parts_keep_their_normal_page_and_accept_tagalog(world):  # noqa: F811
    client, _web, _relay = world
    page = client.get("/part/ch3.locale").text
    assert "Hakbang 1" not in page and "Create draft" in page and "Tagalog o Taglish" in page


@pytest.mark.parametrize(("text", "value"), [("apatnapu", 40), ("apatnapu't lima", 45), ("tatlong araw", 3), ("labindalawa", 12), ("dalawang daan", 200), ("isang libo", 1000), ("sampung estudyante", 10)])
def test_tagalog_numbers_are_understood(text, value):
    assert float(value) in tagalog_numbers(text)


@pytest.mark.parametrize("name", ["G. Santos", "Gng. Reyes", "Bb. Cruz", "Mang Tomas", "Aling Nena", "Kuya Jun", "Ate Liza Ramos", "Ginang Bautista"])
def test_filipino_forms_of_address_are_masked(name):
    masker = Masker()
    masked = masker.mask(f"Sabi ni {name} kahapon.")
    assert name not in masked and masker.restore(masked) == f"Sabi ni {name} kahapon."


OPENALEX = {"results": [
    {"title": "Inventory systems in <i>schools</i>", "publication_year": 2023, "doi": "https://doi.org/10.1/x", "type": "article",
     "authorships": [{"author": {"display_name": "Luigi Carlo M. De Jesus"}, "countries": ["PH"]}, {"author": {"display_name": "Ana Reyes"}, "countries": ["PH"]}],
     "primary_location": {"source": {"display_name": "Journal of Computing"}}, "biblio": {"volume": "8", "issue": "2", "first_page": "11", "last_page": "20"},
     "abstract_inverted_index": {"Web": [0], "systems": [1], "reduce": [2], "errors": [3]}},
    {"title": "No abstract study", "publication_year": 2022, "authorships": [{"author": {"display_name": "Jo Smith"}, "countries": ["US"]}], "primary_location": None, "biblio": {}, "abstract_inverted_index": None},
    {"title": "Missing authors", "publication_year": 2022, "authorships": []},
]}


def test_search_returns_only_complete_real_records_with_abstracts_first():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=OPENALEX)

    results = apa.search_studies("school inventory", httpx.Client(transport=httpx.MockTransport(handler)))
    assert "is_retracted" in seen["url"] and "openalex" in seen["url"]
    assert len(results) == 2 and results[0]["notes"] == "Web systems reduce errors"
    first = results[0]
    assert first["authors"] == "De Jesus, L. C. M.; Reyes, A." and first["title"] == "Inventory systems in schools" and first["origin"] == "Local"
    assert first["pages"] == "11–20" and first["link"] == "https://doi.org/10.1/x" and results[1]["origin"] == "Foreign"


def test_philippine_study_is_marked_local_even_without_author_country():
    work = {"title": "School inventory in the Philippines", "publication_year": 2024, "authorships": [{"author": {"display_name": "Ana Cruz"}, "countries": []}], "primary_location": None, "biblio": {}}
    assert apa._openalex_to_source(work)["origin"] == "Local"


def test_search_falls_back_when_the_daily_budget_is_used_up(world, monkeypatch):  # noqa: F811
    client, _web, _relay = world

    def used_up(_query):
        raise apa.LookupError_("429")

    monkeypatch.setattr(apa, "search_studies", used_up)
    monkeypatch.setattr(apa, "lookup", lambda _query: [{"title": "From Crossref"}])
    assert client.post("/api/sources/search", json={"query": "x"}).json()["results"] == [{"title": "From Crossref"}]


def test_search_failure_is_explained():
    def down(_request):
        raise httpx.ConnectError("offline")

    with pytest.raises(apa.LookupError_):
        apa.search_studies("x", httpx.Client(transport=httpx.MockTransport(down)))


def test_only_confirmed_studies_enter_the_library_once(world):  # noqa: F811
    client, web, _relay = world
    source = {"authors": "Cruz, A.", "year": "2023", "title": "A real study", "container": "J", "notes": "Abstract text", "unknown_field": "dropped"}
    assert client.post("/api/sources/add", json={"source": source}).json()["count"] == 1
    assert client.post("/api/sources/add", json={"source": source}).json()["count"] == 1
    saved = web.state.session.inputs("ch2.sources")["sources"][0]
    assert saved["notes"] == "Abstract text" and "unknown_field" not in saved
    assert not client.post("/api/sources/add", json={"source": {"title": " "}}).json()["ok"]


def test_a_citation_from_an_unconfirmed_study_is_flagged(world):  # noqa: F811
    client, web, relay = world
    first_draft(client, relay)
    answer(client, web, 3)
    relay.replies.append({"blocks": [{"type": "paragraph", "text": "Web systems reduce errors (De Jesus, 2023)."}]})
    client.post("/api/part/ch1.background/ai/write")
    assert any(i["rule"] == "V1" for i in web.state.session.draft("ch1.background")["ai"]["issues"])
