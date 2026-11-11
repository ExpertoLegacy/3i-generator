"""School details in Project Setup are fixed, and students cannot change them."""
from tests.test_ai import world  # noqa: F401, I001 - first, because it puts the app in test mode

from app.parts import generators  # noqa: E402


def test_fixed_details_show_read_only_and_ignore_changes(world):  # noqa: F811
    client, web, _relay = world
    page = client.get("/part/setup.people").text
    assert 'value="Jennymine G. Garcia" readonly' in page and 'data-input="panel_chair"' not in page
    client.post("/api/part/setup.people/inputs", json={"panel_chair": "Someone Else", "section": "Turing"})
    client.post("/api/part/setup.school/inputs", json={"adviser": "Someone Else"})
    session = web.state.session
    assert session.inputs("setup.people")["panel_chair"] == "Jennymine G. Garcia"
    assert session.inputs("setup.school")["adviser"] == "Erickson N. Glodo"
    assert session.inputs("setup.system")["dev_model"] == "Agile"
    facts = generators.build_facts(session)
    assert facts["adviser_upper"] == "ERICKSON N. GLODO"
    assert facts["principal_upper"] == "MARVIN A. BATOY" and facts["principal_title"] == "School Principal III"
    assert facts["panel_member_2_upper"] == "CASELYN S. DAUZ" and facts["month_year"] == "November 2026"
    assert facts["strand_section"] == "Information and Communications Technology – Turing"
