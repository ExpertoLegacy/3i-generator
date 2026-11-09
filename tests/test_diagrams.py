"""Phase 5 acceptance checks: offline diagrams, AI proposals, and labeled AI images."""
import io
import json
import socket

import httpx
import pytest
from docx import Document
from PIL import Image

from app.diagrams import DiagramError, clean_spec, draw, starter
from app.diagrams.render import SMALLEST_READABLE_POINTS, build_dot
from app.parts import spec as spec_module
from app.parts.export import AI_LABEL, run_checks
from tests.test_ai import LONG_NOTE, world  # noqa: F401
from tests.test_app_flow import fill_all


def node(i, label, kind="", details=""):
    return {"id": i, "label": label, "type": kind, "details": details}


def edge(a, b, label=""):
    return {"from": a, "to": b, "label": label}


PHASES = ["Requirements Planning and Analysis", "System Design", "Development (Coding)", "Testing", "Deployment and Evaluation"]
SAMPLES = {
    "ipo": {"nodes": [node("a", "Interview findings from the department", "Input"), node("b", "PHP, Laravel, and MySQL", "Input"), *[node(f"p{i}", t, "Process") for i, t in enumerate(PHASES)], node("o", "A developed and evaluated system", "Output")], "options": {"feedback": True}},
    "model": {"nodes": [node(f"p{i}", t) for i, t in enumerate(PHASES)], "options": {"cycle": True}},
    "architecture": {"nodes": [node("u", "Teachers and Custodian", "User"), node("w", "Web browser interface", "Interface"), node("s", "Laravel application server", "Server"), node("d", "MySQL database", "Database"), node("m", "School email service", "External service")], "edges": [edge("u", "w", "requests"), edge("w", "s", "HTTP"), edge("s", "d", "queries"), edge("s", "m", "notices")]},
    "usecase": {"nodes": [node("a", "Administrator", "Actor"), node("b", "Custodian", "Actor"), node("c", "Teacher", "Actor"), *[node(f"u{i}", t, "Use case") for i, t in enumerate(["Manage user accounts", "Manage inventory", "Record borrowing", "Request equipment", "Generate reports"])]], "edges": [edge("a", "u0"), edge("a", "u4"), edge("b", "u1"), edge("b", "u2"), edge("c", "u3")]},
    "dfd": {"nodes": [node("t", "Teacher", "External entity"), node("c", "Custodian", "External entity"), node("p", "Process borrowing request", "Process"), node("q", "Update inventory", "Process"), node("r", "Generate reports", "Process"), node("d", "Equipment", "Data store"), node("e", "Borrowing records", "Data store")], "edges": [edge("t", "p", "request"), edge("p", "e", "transaction"), edge("p", "c", "notice"), edge("c", "q", "update"), edge("q", "d", "stock"), edge("d", "r", "stock data"), edge("e", "r", "history"), edge("r", "c", "report")]},
    "erd": {"nodes": [node("u", "users", "", "user_id PK\nfull_name\nrole"), node("e", "equipment", "", "equipment_id PK\nname\nquantity"), node("b", "borrowings", "", "borrowing_id PK\nuser_id FK\nequipment_id FK\nborrowed_at")], "edges": [edge("u", "b", "1:N"), edge("e", "b", "1:N")]},
}


@pytest.mark.parametrize("kind", SAMPLES)
def test_every_kind_is_legible_at_print_size(kind):
    result = draw(kind, SAMPLES[kind], "LabTrack")
    with Image.open(io.BytesIO(result.png)) as image:
        assert image.format == "PNG" and min(image.size) > 200
    assert result.printed_font_points >= 10 and not result.warnings


def test_drawing_needs_no_network(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError("network used")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    assert draw("usecase", SAMPLES["usecase"], "LabTrack").png


def test_crowded_diagram_warns_instead_of_printing_unreadably():
    nodes = [node(f"t{i}", f"table_number_{i}", "", "\n".join(f"a_rather_long_field_{j}" for j in range(14))) for i in range(14)]
    edges = [edge("t0", f"t{i}", "1:N") for i in range(1, 14)]
    result = draw("erd", {"nodes": nodes, "edges": edges}, "X")
    assert result.printed_font_points < SMALLEST_READABLE_POINTS and "split" in result.warnings[0]


@pytest.mark.parametrize("label", [
    'A"]; node [image="C:/Windows/win.ini"]; x [label="',
    "<B>bold</B> & <IMG SRC='C:/secret.png'/>",
    "}} digraph evil {{",
    "Ñiño's “quoted” name",
])
def test_labels_cannot_inject_graphviz(label, tmp_path):
    spec = {"nodes": [node("a", label, "User"), node("b", "Database", "Database")], "edges": [edge("a", "b", label[:40])]}
    assert draw("architecture", spec, label).png
    source, _ = build_dot("architecture", clean_spec("architecture", spec), tmp_path, label)
    assert "image=" not in source.replace("image=&quot;", "") and "<IMG" not in source
    assert source.count(" -> ") == 1 and source.count(" [shape") == 2  # the text added no boxes or arrows


@pytest.mark.parametrize("bad_id", ['a [image="C:/x.png"]; b', "1a", "node", "a b", 'x"', "subgraph", "</TD>"])
def test_node_ids_cannot_inject_graphviz(bad_id, tmp_path):
    raw = {"nodes": [node(bad_id, "One", "User"), node("b", "Two", "Server")], "edges": [edge(bad_id, "b")]}
    spec = clean_spec("architecture", raw)
    assert spec["nodes"][0]["id"] == "n1" and spec["edges"] == [{"from": "n1", "to": "b", "label": ""}]
    source, _ = build_dot("architecture", spec, tmp_path, "S")
    assert "image=" not in source
    assert draw("architecture", raw, "S").png


def test_cleaning_drops_bad_connections_and_blank_boxes():
    spec = clean_spec("architecture", {"nodes": [node("a", "One", "Martian"), node("b", ""), node("c", "Three", "Server")], "edges": [edge("a", "c"), edge("a", "ghost"), edge("a", "a")]})
    assert [n["id"] for n in spec["nodes"]] == ["a", "c"] and spec["nodes"][0]["type"] == "User" and len(spec["edges"]) == 1


def test_clear_messages_for_incomplete_tables():
    with pytest.raises(DiagramError, match="Input, Process, and Output"):
        draw("ipo", {"nodes": [node("a", "Only input", "Input")]})
    with pytest.raises(DiagramError, match="connections"):
        draw("erd", {"nodes": [node("a", "t1"), node("b", "t2")], "edges": []})
    with pytest.raises(DiagramError, match="Add at least one"):
        draw("model", {"nodes": []})


def test_starters_use_only_the_students_answers():
    ipo = starter("ipo", {"input": "Interviews\n- Laravel", "process": "Design", "output": "A system"}, {})
    assert [(n["label"], n["type"]) for n in ipo["nodes"]] == [("Interviews", "Input"), ("Laravel", "Input"), ("Design", "Process"), ("A system", "Output")]
    assert starter("model", {}, {"dev_model": "Agile (Scrum)"})["options"]["cycle"] is True
    assert starter("model", {}, {"dev_model": "Waterfall"})["options"]["cycle"] is False
    cases = starter("usecase", {}, {"roles": "Teacher", "modules": "Reports"})
    assert {n["label"] for n in cases["nodes"]} == {"Teacher", "Reports"} and len(cases["edges"]) == 1
    assert starter("erd", {}, {})["nodes"] == []


def drawn(client, key, kind, spec):
    return client.post(f"/api/part/{key}/diagram/draw", json={"kind": kind, "spec": spec}).json()


def figure_captions(path) -> list[str]:
    return [p.text for p in Document(path).paragraphs if p.text.startswith("Figure ")]


def test_drawn_diagrams_reach_word_with_numbers_and_replace_earlier_drawings(world):  # noqa: F811
    client, web, relay = world
    fill_all(client, diagrams=False)
    assert drawn(client, "ch1.framework", "ipo", SAMPLES["ipo"])["ok"]
    assert drawn(client, "ch1.framework", "ipo", SAMPLES["ipo"])["ok"]
    assert len(web.state.session.attachments("ch1.framework")) == 1
    assert drawn(client, "ch3.method.overview", "model", SAMPLES["model"])["ok"]
    assert drawn(client, "ch4.design.architecture", "architecture", SAMPLES["architecture"])["ok"]
    assert drawn(client, "ch4.design.usecase", "dfd", SAMPLES["dfd"])["ok"]
    assert drawn(client, "ch4.design.database", "erd", SAMPLES["erd"])["ok"]
    assert not [i for i in run_checks(web.state.session) if "diagram" in i["message"].lower()]
    assert client.post("/api/build", json={"final": False}).json()["ok"]
    assert len(Document(web.state.last_export).inline_shapes) == 5
    captions = "\n".join(figure_captions(web.state.last_export))
    assert "Figure 1. Conceptual Framework" in captions and "Data Flow Diagram of" in captions and "Use Case Diagram" not in captions
    assert not any(r.url.path in ("/v1/text", "/v1/image") for r in relay.requests)


def test_table_survives_reload_and_stale_drawing_is_flagged(world):  # noqa: F811
    client, web, _relay = world
    drawn(client, "ch4.design.database", "erd", SAMPLES["erd"])
    changed = json.loads(json.dumps(SAMPLES["erd"]))
    changed["nodes"][0]["label"] = "accounts"
    client.post("/api/part/ch4.design.database/diagram", json={"kind": "erd", "spec": changed})
    page = client.get("/part/ch4.design.database").text
    assert "accounts" in page and "after drawing" in page
    assert any("Draw it again" in i["message"] for i in run_checks(web.state.session))
    drawn(client, "ch4.design.database", "erd", changed)
    assert not any("Draw it again" in i["message"] for i in run_checks(web.state.session))


def test_removing_a_drawing_keeps_the_table_and_requires_a_figure_again(world):  # noqa: F811
    client, web, _relay = world
    drawn(client, "ch4.design.database", "erd", SAMPLES["erd"])
    attachment = web.state.session.attachments("ch4.design.database")[0]["id"]
    client.post(f"/api/attachment/{attachment}/delete")
    page = client.get("/part/ch4.design.database").text
    assert "borrowings" in page and "diagram-preview" not in page
    assert any("entity relationship diagram" in i["message"].lower() for i in run_checks(web.state.session))


def test_diagram_controls_work_without_a_relay(world, monkeypatch):  # noqa: F811
    client, _web, _relay = world
    monkeypatch.setenv("RRG_RELAY_URL", "")
    page = client.get("/part/ch1.framework").text
    assert "Draw diagram" in page and "Propose with AI" not in page


# ---- AI proposals --------------------------------------------------------------------------


def background_for_usecase(client):
    client.post("/api/part/ch4.design.usecase/inputs", json={"text": f"{LONG_NOTE} Maria Clara Dela Cruz tested it with the custodian."})


def test_proposal_fills_the_table_but_draws_nothing(world):  # noqa: F811
    client, web, relay = world
    background_for_usecase(client)
    relay.replies.append({"nodes": [{"id": 1, "label": "{{PROPONENT_1}}", "type": "Actor"}, {"id": 2, "label": "Borrow items", "type": "Spaceship"}], "edges": [{"from": 1, "to": 2}, {"from": 1, "to": 99}]})
    assert client.post("/api/part/ch4.design.usecase/ai/diagram", json={"kind": "usecase"}).json()["ok"]
    saved = web.state.session.diagram("ch4.design.usecase")
    assert saved["spec"]["nodes"][0]["label"] == "Maria Clara Dela Cruz"
    assert saved["spec"]["nodes"][1]["type"] == "Actor" and len(saved["spec"]["edges"]) == 1
    assert web.state.session.attachments("ch4.design.usecase") == []
    assert "AI proposed this table" in client.get("/part/ch4.design.usecase").text
    assert "Dela Cruz" not in relay.sent_text() and relay.text_bodies()[0]["mode"] == "diagram"


def test_proposal_is_refused_without_real_inputs(world):  # noqa: F811
    client, _web, relay = world
    assert not client.post("/api/part/ch4.design.database/ai/diagram", json={"kind": "erd"}).json()["ok"]
    assert not relay.text_bodies()


def test_proposal_log_records_acceptance_or_edits(world):  # noqa: F811
    client, web, relay = world
    background_for_usecase(client)
    relay.replies.append({"nodes": [{"id": "a", "label": "Teacher", "type": "Actor"}, {"id": "b", "label": "Borrow items", "type": "Use case"}], "edges": [{"from": "a", "to": "b"}]})
    client.post("/api/part/ch4.design.usecase/ai/diagram", json={"kind": "usecase"})
    proposal = web.state.session.diagram("ch4.design.usecase")["spec"]
    assert drawn(client, "ch4.design.usecase", "usecase", proposal)["ok"]
    assert web.state.session.ai_events()[-1]["outcome"] == "accepted"
    proposal["nodes"][1]["label"] = "Request equipment"
    drawn(client, "ch4.design.usecase", "usecase", proposal)
    assert web.state.session.ai_events()[-1]["outcome"] == "edited"


# ---- AI images ------------------------------------------------------------------------------


EVIDENCE_PARTS = [key for key, config in spec_module.spec()["attachments"].items() if config.get("evidence")]


def create(client, key, description="a borrowing request form with a table"):
    return client.post(f"/api/part/{key}/ai/image", json={"description": description}).json()


def test_no_evidence_part_can_ever_have_an_image_slot():
    assert len(EVIDENCE_PARTS) >= 8
    slots = {key: entry["images"] for key, entry in spec_module.ai_spec()["parts"].items() if entry.get("images")}
    assert slots == {"setup.system": "logo", "ch3.method.phase2": "mockup", "finish.defense": "background"}
    assert not set(slots) & set(EVIDENCE_PARTS)


@pytest.mark.parametrize("key", EVIDENCE_PARTS + ["ch4.acceptability", "ch1.framework"])
def test_evidence_parts_refuse_ai_images(world, key):  # noqa: F811
    client, web, relay = world
    result = create(client, key)
    assert not result["ok"] and "real files" in result["message"]
    assert not [r for r in relay.requests if r.url.path == "/v1/image"]
    assert 'id="ai-image"' not in client.get(f"/part/{key}").text


def test_an_option_stays_out_of_the_report_until_kept(world):  # noqa: F811
    client, web, _relay = world
    assert create(client, "ch3.method.phase2")["ok"]
    session = web.state.session
    assert session.attachments("ch3.method.phase2") == [] and "ch3.method.phase2" not in session.attachment_parts()
    assert len(session.attachments("ch3.method.phase2", options=True)) == 1
    assert "Use in report" in client.get("/part/ch3.method.phase2").text
    fill_all(client, diagrams=True)
    client.post("/api/build", json={"final": False})
    assert not any(AI_LABEL in c for c in figure_captions(web.state.last_export))


def test_kept_image_is_always_labeled_in_word(world):  # noqa: F811
    client, web, _relay = world
    fill_all(client, diagrams=True)
    option = web.state.session.attachments("ch3.method.phase2", options=True) or None
    assert option is None
    attachment = create(client, "ch3.method.phase2")["attachment"]
    assert client.post(f"/api/attachment/{attachment}/keep").json()["ok"]
    kept = web.state.session.attachments("ch3.method.phase2")[0]
    assert kept["ai"] == 1 and kept["kind"] == "file"
    client.post(f"/api/attachment/{attachment}", json={"caption": "Borrowing screen, definitely real", "consent": False})
    client.post("/api/build", json={"final": False})
    captions = figure_captions(web.state.last_export)
    assert any("Borrowing screen, definitely real" in c and AI_LABEL in c for c in captions)


def test_logo_cannot_be_placed_in_the_report(world):  # noqa: F811
    client, web, _relay = world
    attachment = create(client, "setup.system", "a flask with a checkmark")["attachment"]
    assert not client.post(f"/api/attachment/{attachment}/keep").json()["ok"]
    assert "Save as file" in client.get("/part/setup.system").text


def test_keep_cannot_promote_a_student_upload(world):  # noqa: F811
    client, web, _relay = world
    attachment = web.state.session.add_attachment("ch3.method.phase2", "file", "real.png", "image/png", b"x")
    assert not client.post(f"/api/attachment/{attachment}/keep").json()["ok"]


def test_image_prompt_is_masked_and_logged(world):  # noqa: F811
    client, web, relay = world
    create(client, "ch3.method.phase2", "a dashboard for Maria Clara Dela Cruz and her team")
    assert "Dela Cruz" not in relay.sent_text()
    event = web.state.session.ai_events()[-1]
    usage = json.loads(event["usage"])
    assert (event["kind"], event["model"], usage["slot"], usage["neurons"]) == ("image", "test-image", "mockup", 60)
    assert "{{PROPONENT_1}}" in usage["prompt"]


def test_short_descriptions_and_bad_images_are_refused(world):  # noqa: F811
    client, web, relay = world
    assert "few words" in create(client, "ch3.method.phase2", "form")["message"]
    relay.image = "not base64 !!"
    assert "could not be read" in create(client, "ch3.method.phase2")["message"]
    relay.image = "QUJD"
    assert "could not be read" in create(client, "ch3.method.phase2")["message"]
    assert web.state.session.attachments("ch3.method.phase2", options=True) == []


def test_refused_or_offline_image_changes_nothing(world):  # noqa: F811
    client, web, relay = world
    relay.fail_next = httpx.Response(400, json={"ok": False, "error": "evidence", "message": "AI images cannot be used for screenshots."})
    assert "screenshots" in create(client, "ch3.method.phase2")["message"]
    relay.fail_next = httpx.ReadError("dropped")
    assert "internet" in create(client, "ch3.method.phase2")["message"]
    assert web.state.session.attachments("ch3.method.phase2", options=True) == []
    assert not web.ai.running.locked()


def test_only_a_few_options_are_kept(world):  # noqa: F811
    client, web, _relay = world
    for _ in range(6):
        assert create(client, "ch3.method.phase2")["ok"]
    assert len(web.state.session.attachments("ch3.method.phase2", options=True)) == 4
