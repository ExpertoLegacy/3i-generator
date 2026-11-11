"""Phase 4 acceptance checks. The relay is simulated at the network boundary; everything in the app is real."""
import base64
import io
import json
import os
import sys
from pathlib import Path

os.environ["RRG_NO_TOKEN"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
import pytest  # noqa: E402
from docx import Document  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.ai_client.masking import Masker  # noqa: E402
from app.ai_client.validators import Facts, check_text  # noqa: E402
from app.parts import spec as spec_module  # noqa: E402
from app.session import Session  # noqa: E402
from tests.test_app_flow import fill_all  # noqa: E402

GOOD = {"blocks": [{"type": "paragraph", "text": "The researchers studied the borrowing process at the locale."}], "citations_used": [], "missing_info": []}
LONG_NOTE = "The science department records every borrowed item by hand in a logbook."


def _jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (64, 64), (120, 120, 120)).save(out, format="JPEG")
    return out.getvalue()


class Relay:
    """Stands in for the deployed relay and records every outbound request."""

    def __init__(self):
        self.requests = []
        self.replies = []
        self.registrations = 0
        self.fail_next = None
        self.image = base64.b64encode(_jpeg()).decode("ascii")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/v1/health":
            return httpx.Response(200, json={"ok": True, "prompt_version": "v1"})
        if path == "/v1/register":
            self.registrations += 1
            return httpx.Response(200, json={"ok": True, "install_token": f"token{self.registrations}"})
        if path == "/v1/quota":
            return httpx.Response(200, json={"ok": True, "quota": {"install_remaining": 2000, "overall_remaining": 9000}})
        if self.fail_next:
            failure, self.fail_next = self.fail_next, None
            if isinstance(failure, Exception):
                raise failure
            return failure
        if path == "/v1/image":
            return httpx.Response(200, json={"ok": True, "image_base64": self.image, "model": "test-image", "prompt_version": "v1", "usage": {"neurons": 60}})
        content = self.replies.pop(0) if self.replies else GOOD
        return httpx.Response(200, json={
            "ok": True, "text": content if isinstance(content, str) else json.dumps(content), "model": "test-model", "prompt_version": "v1",
            "usage": {"input_tokens": 2000, "output_tokens": 800, "neurons": 40}, "quota": {"install_remaining": 2000, "overall_remaining": 9000},
        })

    def sent_text(self) -> str:
        return "\n".join(r.content.decode("utf-8") + str(dict(r.headers)) for r in self.requests)

    def text_bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.url.path == "/v1/text"]


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("RRG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RRG_RELAY_URL", "https://relay.test")
    from app import web

    relay = Relay()
    web.ai.client.http = httpx.Client(transport=httpx.MockTransport(relay))
    web.ai.last_ready = None
    monkeypatch.setattr(web.state.settings, "file", tmp_path / "settings.json")
    web.state.settings.data.pop("ai", None)
    web.state.session = Session.create(tmp_path / "group.rrg")
    with TestClient(web.app) as client:
        client.post("/api/part/setup.school/inputs", json={"adviser": "Erickson N. Glodo", "principal": "Marvin A. Batoy", "school_name": "Cabiao Senior High School"})
        client.post("/api/part/setup.people/inputs", json={"proponents": [{"name": "Maria Clara Dela Cruz"}, {"name": "Jose P. Rizalino Jr."}], "panel_chair": "Jennymine G. Garcia"})
        client.post("/api/part/setup.system/inputs", json={"system_name": "LabTrack", "platform": "web-based", "locale": "the school"})
        yield client, web, relay
    web.state.session.close()
    web.state.session = None


def fill_background(client, **extra):
    client.post("/api/part/ch1.background/inputs", json={"context": LONG_NOTE, "situation": LONG_NOTE, "problems": LONG_NOTE, **extra})


# ---- no setup, and honest states when AI is unavailable ---------------------------------


def test_fresh_install_registers_without_any_step(world):
    client, web, relay = world
    status = client.get("/api/ai/status").json()
    assert status == {"state": "ready", "tasks_left": 80, "update": False}
    assert relay.registrations == 1
    client.get("/api/ai/status")
    assert relay.registrations == 1
    assert "install_id" in web.state.settings.data["ai"]


def test_rotated_secret_registers_again(world):
    client, _web, relay = world
    fill_background(client)
    client.get("/api/ai/status")
    relay.fail_next = httpx.Response(401, json={"ok": False, "message": "register"})
    assert client.post("/api/part/ch1.background/ai/write").json()["ok"]
    assert relay.registrations == 2


def test_no_ai_controls_when_relay_is_not_set_up(world, monkeypatch):
    client, _web, _relay = world
    monkeypatch.setenv("RRG_RELAY_URL", "")
    page = client.get("/part/ch1.background").text
    assert "Draft with AI" not in page and "ai-state" not in page
    assert client.get("/api/ai/status").json() == {"state": "off"}


def test_offline_status_and_message(world):
    client, web, _relay = world

    def down(_request):
        raise httpx.ConnectError("offline")

    web.ai.client.http = httpx.Client(transport=httpx.MockTransport(down))
    status = client.get("/api/ai/status").json()
    assert status["state"] == "unavailable" and "internet" in status["message"]
    fill_background(client)
    result = client.post("/api/part/ch1.background/ai/write").json()
    assert not result["ok"] and "internet" in result["message"]


# ---- required real inputs ---------------------------------------------------------------


def test_chapter_4_stays_disabled_without_data(world):
    client, _web, relay = world
    result = client.post("/api/part/ch4.acceptability/ai/write").json()
    assert not result["ok"] and "Weighted means" in result["message"]
    assert not relay.text_bodies()
    assert "data-blocked" in client.get("/part/ch4.acceptability").text


@pytest.mark.parametrize("key", ["ch4.testing", "ch4.difference", "ch4.enhancements", "ch5.summary"])
def test_results_parts_refuse_without_real_inputs(world, key):
    client, _web, relay = world
    assert not client.post(f"/api/part/{key}/ai/write").json()["ok"]
    assert not relay.text_bodies()


def test_chapter_4_opens_once_data_exists_and_sends_computed_values(world):
    client, web, relay = world
    from app.parts import generators as g

    scores = g.inputs_for(web.state.session, "ch4.acceptability")["scores"]
    scores[0]["experts"] = "4.53"
    client.post("/api/part/ch4.acceptability/inputs", json={"scores": scores})
    assert client.post("/api/part/ch4.acceptability/ai/write").json()["ok"]
    sent = relay.text_bodies()[0]["masked_input"]
    assert sent["tables"][0]["ref"] == "{{ref:table:acceptability}}"
    assert "4.53" in json.dumps(sent["tables"]) and "Highly Acceptable" in json.dumps(sent["tables"])


def test_saving_answers_reports_what_is_still_missing(world):
    client, _web, _relay = world
    partial = client.post("/api/part/ch1.background/inputs", json={"context": LONG_NOTE, "situation": LONG_NOTE}).json()
    assert len(partial["ai_blocked"]) == 1 and "problems" in partial["ai_blocked"][0].lower()
    complete = client.post("/api/part/ch1.background/inputs", json={"context": LONG_NOTE, "situation": LONG_NOTE, "problems": LONG_NOTE}).json()
    assert complete["ai_blocked"] == []


def test_one_word_answers_do_not_unlock_ai(world):
    client, _web, _relay = world
    client.post("/api/part/ch1.background/inputs", json={"context": "x", "situation": LONG_NOTE, "problems": LONG_NOTE})
    assert "more detail" in client.post("/api/part/ch1.background/ai/write").json()["message"]


# ---- privacy -------------------------------------------------------------------------------


def test_no_personal_data_in_outbound_requests(world):
    client, _web, relay = world
    note = (
        "Maria Clara Dela Cruz and Ms. Dela Cruz asked Mr. Fernandez Abad, while Erickson N. Glodo advised. Glodo approved. "
        "Reach maria.cruz@school.edu.ph or 0917-123-4567 or +63 917 765 4321. Student LRN 123456789012. Jennymine G. Garcia chaired, and Marvin A. Batoy signed."
    )
    fill_background(client, situation=note)
    assert client.post("/api/part/ch1.background/ai/write").json()["ok"]
    sent = relay.sent_text()
    for secret in ("Dela Cruz", "Maria Clara", "Fernandez", "Glodo", "Erickson", "maria.cruz", "917", "123456789012", "Garcia", "Batoy", "Rizalino"):
        assert secret not in sent, secret
    assert "{{ADVISER}}" in sent and "{{PROPONENT_1}}" in sent and "{{EMAIL_1}}" in sent and "{{LRN_1}}" in sent


def test_names_are_restored_in_the_draft_and_contacts_are_reported(world):
    client, web, relay = world
    fill_background(client, situation=f"{LONG_NOTE} Call 09171234567 today.")
    relay.replies.append({"blocks": [{"type": "paragraph", "lead": "{{ADVISER}}", "text": "{{PROPONENT_1}} met {{PHONE_1}}."}]})
    client.post("/api/part/ch1.background/ai/write")
    draft = web.state.session.draft("ch1.background")
    assert "Erickson N. Glodo.</strong> Maria Clara Dela Cruz met 09171234567." in draft["html"]
    assert draft["ai"]["hidden_contacts"] == 1


def test_attachments_are_never_sent(world):
    client, web, relay = world
    web.state.session.add_attachment("ch1.background", "file", "photo.png", "image/png", b"PHOTO-BYTES-XYZ")
    fill_background(client)
    client.post("/api/part/ch1.background/ai/write")
    assert "PHOTO-BYTES-XYZ" not in relay.sent_text() and "photo.png" not in relay.sent_text()


def test_masker_leaves_ordinary_words_and_citations():
    masker = Masker(keep_surnames={"Cruz"})
    masker.add("Grace Cruz", "PROPONENT_1")
    masked = masker.mask("There is a grace period (Cruz, 2024). Grace Cruz spoke.")
    assert "grace period (Cruz, 2024)" in masked and "{{PROPONENT_1}} spoke" in masked
    assert masker.restore(masked) == "There is a grace period (Cruz, 2024). Grace Cruz spoke."


# ---- integrity checks -----------------------------------------------------------------------


def facts(payload: str = "", **extra) -> Facts:
    return Facts(payload_text=payload, sources=[{"authors": "Santos, A.", "year": "2022", "title": "Real"}], **extra)


def rules(issues):
    return {i["rule"] for i in issues}


def test_invented_citation_is_flagged():
    assert "V1" in rules(check_text("Records improve (Ghost, 2021).", facts()))
    assert "V1" not in rules(check_text("Records improve (Santos, 2022).", facts()))
    assert "V1" in rules(check_text("Fine text.", facts(), citations_used=["Ghost, 2021"]))


@pytest.mark.parametrize("text", ["About 73 percent were lost.", "The mean was 4.87.", "There were 1,250 records.", "Thirty-five respondents answered."])
def test_invented_numbers_are_flagged(text):
    assert "V2" in rules(check_text(text, facts('{"score": "4.5"}')))


@pytest.mark.parametrize("text", ["The mean was 4.50.", "Phase 3 followed.", "ISO/IEC 25010 was used under RA 10173.", "Two groups answered."])
def test_real_numbers_are_accepted(text):
    assert "V2" not in rules(check_text(text, facts('{"score": "4.5"}')))


def test_invented_quotation_is_flagged():
    assert "V2" in rules(check_text("One said “this system saved every record we ever had”.", facts()))


def test_placeholders_voice_tense_and_length():
    assert "V3" in rules(check_text("Text {{NAME_9}} remains.", facts()))
    assert "V3" in rules(check_text("The school [insert name] agreed.", facts()))
    assert "V5" in rules(check_text("We built it.", facts()))
    assert "V5" in rules(check_text("The system will record items.", facts(tense="past")))
    assert "V5" not in rules(check_text("The system will record items.", facts(tense="future")))
    assert "V4" in rules(check_text("Too short.", facts(words=[150, 250])))


def test_table_mentions_and_appendix_references():
    token = "{{ref:table:acceptability}}"
    assert "V7" in rules(check_text("No mention.", facts(tables=[token], valid_refs={token})))
    assert "V7" not in rules(check_text(f"As {token} shows.", facts(tables=[token], valid_refs={token})))
    assert "V8" in rules(check_text("See {{ref:table:madeup}}.", facts()))
    off = "{{ref:appendix:editing}}"
    assert "V8" in rules(check_text(f"See {off}.", facts(valid_refs={off}, off_appendices={"app.editing"})))


def test_fabrications_from_the_model_reach_the_student(world):
    client, web, relay = world
    fill_background(client)
    relay.replies.append({"blocks": [{"type": "paragraph", "text": "About 73 percent of records were lost (Ghost, 2019)."}], "citations_used": ["Ghost, 2019"], "missing_info": ["Weekly transaction count"]})
    client.post("/api/part/ch1.background/ai/write")
    page = client.get("/part/ch1.background").text
    assert "Ghost, 2019" in page and "73" in page and "Weekly transaction count" in page and "AI draft." in page
    from app.parts.export import run_checks

    messages = [i["message"] for i in run_checks(web.state.session) if i["key"] == "ch1.background"]
    assert any("items to confirm" in m for m in messages) and any("Ghost" in m for m in messages)


def test_editing_the_draft_recomputes_the_flags(world):
    client, web, relay = world
    fill_background(client)
    relay.replies.append({"blocks": [{"type": "paragraph", "text": "About 73 percent were lost."}]})
    client.post("/api/part/ch1.background/ai/write")
    assert web.state.session.draft("ch1.background")["ai"]["issues"]
    result = client.post("/api/part/ch1.background/draft", json={"html": "<p>The records were kept by hand.</p>"}).json()
    assert result["ai_issues"] == [] and web.state.session.draft("ch1.background")["ai"]["issues"] == []


# ---- reliability ----------------------------------------------------------------------------


def test_disconnect_mid_request_loses_no_work(world):
    client, web, relay = world
    fill_background(client)
    client.post("/api/part/ch1.background/generate")
    client.post("/api/part/ch1.background/draft", json={"html": "<p>My careful edits.</p>"})
    relay.fail_next = httpx.ReadError("connection dropped")
    result = client.post("/api/part/ch1.background/ai/write").json()
    assert not result["ok"] and "internet" in result["message"]
    assert web.state.session.draft("ch1.background")["html"] == "<p>My careful edits.</p>"
    assert web.state.session.inputs("ch1.background")["context"] == LONG_NOTE
    assert not web.ai.running.locked()


def test_quota_refusal_changes_nothing(world):
    client, web, relay = world
    fill_background(client)
    relay.fail_next = httpx.Response(429, json={"ok": False, "error": "pool_used_up", "message": "Today's shared AI allowance is used up."})
    result = client.post("/api/part/ch1.background/ai/write").json()
    assert not result["ok"] and "allowance" in result["message"]
    assert web.state.session.draft("ch1.background") is None


def test_bad_json_is_retried_once_with_the_error(world):
    client, _web, relay = world
    fill_background(client)
    relay.replies += ["not json at all", GOOD]
    assert client.post("/api/part/ch1.background/ai/write").json()["ok"]
    bodies = relay.text_bodies()
    assert len(bodies) == 2 and "JSON" in bodies[1]["fix_hint"]


def test_fenced_json_is_accepted(world):
    client, web, relay = world
    fill_background(client)
    relay.replies.append("Here it is:\n```json\n" + json.dumps(GOOD) + "\n```")
    assert client.post("/api/part/ch1.background/ai/write").json()["ok"]
    assert "borrowing process" in web.state.session.draft("ch1.background")["html"]


def test_two_bad_replies_leave_the_draft_alone(world):
    client, web, relay = world
    fill_background(client)
    relay.replies += ["bad", "still bad"]
    result = client.post("/api/part/ch1.background/ai/write").json()
    assert not result["ok"] and web.state.session.draft("ch1.background") is None
    assert web.state.session.ai_events()[-1]["outcome"] == "unusable"


def test_no_blocks_reports_what_is_missing(world):
    client, web, relay = world
    fill_background(client)
    relay.replies.append({"blocks": [], "missing_info": ["How often the process happens"]})
    result = client.post("/api/part/ch1.background/ai/write").json()
    assert not result["ok"] and "How often" in result["message"] and web.state.session.draft("ch1.background") is None


def test_one_request_at_a_time(world):
    client, web, _relay = world
    fill_background(client)
    with web.ai.running:
        result = client.post("/api/part/ch1.background/ai/write").json()
    assert not result["ok"] and "still running" in result["message"]


def test_previous_version_can_be_restored_after_ai(world):
    client, web, _relay = world
    fill_background(client)
    client.post("/api/part/ch1.background/generate")
    before = web.state.session.draft("ch1.background")["html"]
    client.post("/api/part/ch1.background/ai/write")
    assert web.state.session.draft("ch1.background")["html"] != before
    client.post("/api/part/ch1.background/restore")
    assert web.state.session.draft("ch1.background")["html"] == before


# ---- cache and log --------------------------------------------------------------------------


def test_same_inputs_use_cache_until_student_asks_again(world):
    client, web, relay = world
    fill_background(client)
    client.post("/api/part/ch1.background/ai/write")
    assert len(relay.text_bodies()) == 1
    client.post("/api/part/ch1.background/restore")
    client.post("/api/part/ch1.background/generate")
    assert client.post("/api/part/ch1.background/ai/write").json()["cached"] is True
    assert len(relay.text_bodies()) == 1
    assert client.post("/api/part/ch1.background/ai/write").json()["cached"] is False
    assert len(relay.text_bodies()) == 2


def test_every_generation_is_logged_with_what_the_student_did(world):
    client, web, _relay = world
    fill_background(client)
    client.post("/api/part/ch1.background/ai/write")
    event = web.state.session.ai_events()[-1]
    assert (event["part_key"], event["kind"], event["model"], event["prompt_version"], event["outcome"]) == ("ch1.background", "write", "test-model", "v1", "generated")
    assert json.loads(event["usage"])["neurons"] == 40
    client.post("/api/part/ch1.background/review", json={"reviewed": True})
    assert web.state.session.ai_events()[-1]["outcome"] == "accepted"
    client.post("/api/part/ch1.background/draft", json={"html": "<p>Changed.</p>"})
    client.post("/api/part/ch1.background/review", json={"reviewed": True})
    assert web.state.session.ai_events()[-1]["outcome"] == "edited"


def test_ai_text_starts_unreviewed_and_blocks_final(world):
    client, web, _relay = world
    fill_background(client)
    client.post("/api/part/ch1.background/ai/write")
    assert web.state.session.status()["ch1.background"] == "draft"
    from app.parts.export import run_checks

    assert any(i["key"] == "ch1.background" and i["blocking"] and "reviewed" in i["message"] for i in run_checks(web.state.session))


# ---- suggest, check, and the Word file ----------------------------------------------------


def test_suggest_is_limited_to_planning_fields(world):
    client, _web, relay = world
    relay.replies.append({"suggestions": ["No offline mode", "Real one (Ghost, 2020)"]})
    result = client.post("/api/part/ch1.scope/ai/suggest", json={"field": "limits"}).json()
    assert result["suggestions"] == ["No offline mode"]
    assert not client.post("/api/part/ch1.scope/ai/suggest", json={"field": "scope"}).json()["ok"]
    assert not client.post("/api/part/ch4.acceptability/ai/suggest", json={"field": "scores"}).json()["ok"]


def test_no_evidence_field_offers_suggestions():
    evidence = {"findings", "scores", "cases", "t", "p", "experts_mean", "users_mean"}
    for key, entry in spec_module.ai_spec()["parts"].items():
        assert not (set(entry.get("suggest", {})) & evidence), key
        if key.startswith("ch4."):
            assert "suggest" not in entry, key


def test_check_needs_a_draft_and_returns_both_kinds_of_finding(world):
    client, _web, relay = world
    fill_background(client)
    assert not client.post("/api/part/ch1.background/ai/check").json()["ok"]
    client.post("/api/part/ch1.background/draft", json={"html": "<p>x</p>"})
    client.post("/api/part/ch1.background/generate")
    client.post("/api/part/ch1.background/draft", json={"html": "<p>We lost (Ghost, 2020).</p>"})
    relay.replies.append({"findings": [{"severity": "fix", "message": "State how often {{PROPONENT_1}} did this."}]})
    result = client.post("/api/part/ch1.background/ai/check").json()
    assert "Maria Clara Dela Cruz" in result["findings"][0]["message"]
    assert {"V1", "V5"} <= {r["rule"] for r in result["rules"]}
    assert "We lost" in relay.text_bodies()[0]["masked_input"]["draft"]


def test_check_sees_real_characters_not_html_entities(world):
    client, _web, relay = world
    fill_background(client)
    client.post("/api/part/ch1.background/generate")
    client.post("/api/part/ch1.background/draft", json={"html": "<p>Records improve (Santos &amp; Reyes, 2022).</p>"})
    client.post("/api/part/ch1.background/ai/check")
    draft = relay.text_bodies()[0]["masked_input"]["draft"]
    assert "Santos & Reyes" in draft and "&amp;" not in draft


def test_ai_draft_exports_to_word_with_tables_kept_and_tokens_resolved(world, tmp_path):
    client, web, relay = world
    fill_all(client)
    relay.replies.append({"blocks": [{"type": "paragraph", "text": "As {{ref:table:acceptability}} shows, {{ADVISER}} reviewed the results."}]})
    assert client.post("/api/part/ch4.acceptability/ai/write").json()["ok"]
    assert client.post("/api/build", json={"final": False}).json()["ok"]
    document = Document(web.state.last_export)
    text = "\n".join(p.text for p in document.paragraphs)
    assert "reviewed the results" in text and "{{" not in text
    assert any("Overall Weighted Mean" in c.text for t in document.tables for r in t.rows for c in r.cells)


def test_relay_prompts_match_the_spec():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import build_relay_prompts

    stored = json.loads(build_relay_prompts.TARGET.read_text(encoding="utf-8"))
    assert stored == build_relay_prompts.build(), "Run tools/build_relay_prompts.py"
    assert "Length: 2–3 pages" in stored["parts"]["ch1.background"]["guideline"]
