"""Forms read in English first, with Tagalog in small type, and show one step at a time."""
import re
from pathlib import Path

from tests.test_ai import world  # noqa: F401, I001 - first, because it puts the app in test mode
from tests.test_guided import FIRST, SETUP  # noqa: E402

from app import web  # noqa: E402
from app.parts import spec as spec_module  # noqa: E402

TEMPLATE = Path(web.BASE) / "app" / "templates" / "part.html"
SHORT = {"DOI or URL", "A", "B", "C", "ID", "Volume", "Issue", "Pages", "Page", "Status", "Name", "Group", "Module", "Feature", "Software", "Hardware", "Purpose", "Version", "Component", "Actual", "Expected", "Scenario", "Activity", "Indicator", "Characteristic", "IT experts", "End-users", "t-value", "p-value", "Population (N)", "Sample (n)", "Questions from the system", "Yours"}


def test_every_instruction_in_the_template_has_tagalog():
    text = TEMPLATE.read_text(encoding="utf-8")
    table = spec_module.translations()
    literals = re.findall(r'\{\{ (?:say|both)\("([^"]+)"', text)
    assert len(literals) > 25
    assert not [t for t in literals if t not in table]


def test_every_form_label_has_tagalog_except_short_terms():
    table = spec_module.translations()
    missing = []
    for part in spec_module.all_parts():
        for field in part["inputs"]:
            for label in [field.get("label"), *(s.get("label") for s in field.get("fields", []))]:
                if label and label not in table and label not in SHORT and not re.match(r"(IT experts|End-users) (mean|SD)$", label):
                    missing.append(label)
    assert not missing, missing


def test_tagalog_is_smaller_and_comes_after_the_english(world):  # noqa: F811
    client, _web, relay = world
    client.post("/api/part/setup.system/inputs", json=SETUP)
    relay.replies.append(FIRST)
    client.post("/api/part/ch1.background/ai/guided")
    page = client.get("/part/ch1.background").text
    assert page.index("Question 1 in English?") < page.index("Tanong 1 sa Tagalog?")
    assert '<small class="tl" lang="tl">Tanong 1 sa Tagalog?</small>' in page
    assert "Hakbang" not in page and "Answer a few questions" in page


def test_setup_form_is_english_first(world):  # noqa: F811
    client, _web, _relay = world
    page = client.get("/part/setup.system").text
    assert 'System name<small class="tl" lang="tl">Pangalan ng sistema</small>' in page


def test_app_messages_are_english_first_everywhere():
    for name in ("app/web.py", "app/ai_client/service.py", "app/repo_analysis.py", "app/static/app.js"):
        text = (Path(web.BASE) / name).read_text(encoding="utf-8")
        assert not re.search(r'"(?:Hindi|Gawin|Sagutin|Pumili|Aprubahan|Tapusin|Palitan|Alisin|Maglagay|Ikonekta|Kumpletuhin|Walang) [^"|]*\(', text), name


def test_steps_show_what_to_do_now(world):  # noqa: F811
    client, _web, relay = world
    page = client.get("/part/ch1.background").text
    assert re.search(r'class="step now">\s*<h3[^>]*><span>Make the first draft', page)
    assert re.search(r'class="step later">\s*<h3[^>]*><span>Answer a few questions', page)
    client.post("/api/part/setup.system/inputs", json=SETUP)
    relay.replies.append(FIRST)
    client.post("/api/part/ch1.background/ai/guided")
    page = client.get("/part/ch1.background").text
    assert re.search(r'class="step done">\s*<h3[^>]*><span>Make the first draft', page)
    assert re.search(r'class="step now">\s*<h3[^>]*><span>Answer a few questions', page)


def test_guidelines_start_folded_and_one_approve_button(world):  # noqa: F811
    client, _web, _relay = world
    client.post("/api/part/ch1.scope/generate")
    page = client.get("/part/ch1.scope").text
    assert '<details class="guide">' in page and "Approve and continue" in page and 'id="reviewed"' not in page
    client.post("/api/part/ch1.scope/review", json={"reviewed": True})
    page = client.get("/part/ch1.scope").text
    assert 'id="reviewed"' in page and 'id="approve-next"' not in page


def test_library_entries_fold_to_their_titles(world):  # noqa: F811
    client, _web, _relay = world
    client.post("/api/part/ch2.sources/inputs", json={"sources": [{"title": "A real study", "authors": "Cruz, A."}]})
    page = client.get("/part/ch2.sources").text
    assert '<details class="row collapsible" ><summary>A real study</summary>' in page


def test_topics_ask_only_for_the_topic(world):  # noqa: F811
    client, _web, _relay = world
    client.post("/api/part/ch2.literature/inputs", json={"research_topics": [{"topic": "Inventory", "search": "school inventory"}]})
    page = client.get("/part/ch2.literature").text
    assert 'type="hidden" data-field="search" value="school inventory"' in page and "Search words" not in page
