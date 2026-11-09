"""Chapters 3 to 5, the Word master, code analysis, and appendices on the guided path."""
import io
import zipfile

import httpx
import pytest
from docx import Document

from tests.test_ai import world  # noqa: F401, I001 - first, because it puts the app in test mode
from tests.test_guided import SETUP  # noqa: E402
from tests.test_path import built_and_approved, finish_chapter_1  # noqa: E402

from app import repo_analysis  # noqa: E402
from app.parts import generators as g  # noqa: E402
from app.parts import spec as spec_module  # noqa: E402

DRAFT = {"blocks": [{"type": "paragraph", "text": "An automatically drafted section."}], "citations_used": [], "missing_info": []}


def start(client):
    client.post("/api/part/setup.system/inputs", json=SETUP)


def write(client, key, **body):
    return client.post(f"/api/part/{key}/ai/synthesize", json=body).json()


# ---- Chapter 3 ---------------------------------------------------------------------------------


def test_research_design_offers_suggestions_and_a_click_drafts_it(world):  # noqa: F811
    client, web, relay = world
    start(client)
    relay.replies.append({"options": [{"name": "Developmental research", "why_tl": "Bagay ito.", "why_en": "It fits."}]})
    assert client.post("/api/part/ch3.design/ai/options").json()["ok"]
    page = client.get("/part/ch3.design").text
    assert "Developmental research" in page and "Bagay ito." in page
    assert "Choose one" in write(client, "ch3.design")["message"]
    values = web.state.session.inputs("ch3.design")
    values["choice"] = "Developmental research"
    client.post("/api/part/ch3.design/inputs", json=values)
    relay.replies.append(DRAFT)
    assert write(client, "ch3.design")["ok"]
    assert relay.text_bodies()[-1]["masked_input"]["choice"] == "Developmental research"


def test_sampling_has_fixed_choices(world):  # noqa: F811
    client, _web, _relay = world
    page = client.get("/part/ch3.sampling").text
    for name in ("Total enumeration", "Purposive sampling", "Simple random sampling"):
        assert name in page


def test_locale_and_population_stay_student_input(world):  # noqa: F811
    assert spec_module.auto_config("ch3.locale") is None and spec_module.auto_config("ch3.population") is None


def test_instrument_table_is_drafted_from_earlier_work(world):  # noqa: F811
    client, web, relay = world
    start(client)
    relay.replies += [{"rows": [{"characteristic": "Usability", "indicator": "The system is easy to learn.", "extra": "dropped"}]}, DRAFT]
    assert write(client, "ch3.instrument")["ok"]
    assert web.state.session.inputs("ch3.instrument")["indicators"] == [{"characteristic": "Usability", "indicator": "The system is easy to learn."}]


def test_statistics_builds_itself_but_still_needs_approval(world):  # noqa: F811
    client, web, _relay = world
    client.get("/part/ch3.statistics")
    assert web.state.session.draft("ch3.statistics") and web.state.session.status()["ch3.statistics"] == "draft"


def test_instrument_download_waits_for_chapter_3(world):  # noqa: F811
    from tests.test_app_flow import fill_all

    client, web, _relay = world
    assert not client.post("/api/instrument/download").json()["ok"]
    fill_all(client)
    result = client.post("/api/instrument/download").json()
    assert result["ok"] and "Directions" in "\n".join(p.text for p in Document(web.state.last_export).paragraphs)


# ---- Word master -------------------------------------------------------------------------------


def test_chapter_build_needs_every_part_approved(world):  # noqa: F811
    client, _web, _relay = world
    built_and_approved(client, "ch1.background")
    result = client.post("/api/chapter/ch1/build").json()
    assert not result["ok"] and "Statement of the Problem" in result["message"]


@pytest.mark.gated
def test_build_updates_the_master_file_and_preview(world, tmp_path):  # noqa: F811
    client, web, _relay = world
    assert client.get("/api/master-preview").status_code == 204
    finish_chapter_1(client)
    result = client.post("/api/chapter/ch1/build").json()
    assert result["ok"] and "Master.docx" in result["message"]
    preview = client.get("/api/master-preview")
    assert preview.status_code == 200
    text = "\n".join(p.text for p in Document(io.BytesIO(preview.content)).paragraphs)
    assert "Approved text for this part." in text
    assert (web.state.session.path.parent / f"{web.state.session.path.stem} Master.docx").exists()


@pytest.mark.gated
def test_unbuilt_chapters_stay_out_of_the_master(world):  # noqa: F811
    client, web, _relay = world
    finish_chapter_1(client)
    client.post("/api/chapter/ch1/build")
    built_and_approved(client, "ch2.literature", "<p>Literature not built yet.</p>")
    client.post("/api/chapter/ch1/build")
    assert "Literature not built yet" not in "\n".join(p.text for p in Document(io.BytesIO(client.get("/api/master-preview").content)).paragraphs)


@pytest.mark.gated
def test_build_button_shows_on_each_chapter_and_preview_is_on_the_page(world):  # noqa: F811
    client, _web, _relay = world
    page = client.get("/part/ch1.background").text
    assert 'id="build-chapter"' in page and "Build Chapter 1" in page and 'id="master-view"' in page and "Create draft" in page


# ---- Chapter 4 ---------------------------------------------------------------------------------


def repo_zip():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("proj-main/README.md", "LabTrack\nInventory system")
        z.writestr("proj-main/composer.json", '{"require":{"laravel/framework":"^11"}}')
        z.writestr("proj-main/routes/web.php", "Route::get('/items', [ItemController::class, 'index']);")
        z.writestr("proj-main/database/migrations/create_items.php", "Schema::create('items', function ($t) { $t->id(); $t->string('name'); });")
        z.writestr("proj-main/config/database.php", "'password' => 'SuperSecret123',")
        z.writestr("proj-main/.env", "APP_KEY=TOPSECRETENV\nDB_PASSWORD=TOPSECRETENV")
        z.writestr("proj-main/keys/server.pem", "PEMSECRET")
        z.writestr("proj-main/node_modules/lib/index.js", "NODEMODULE")
    return out.getvalue()


def github_client(status=200, body=None):
    def handler(request):
        assert request.url.host == "codeload.github.com"
        return httpx.Response(status, content=repo_zip() if body is None else body)

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_github_digest_reads_code_and_never_secrets():
    result = repo_analysis.from_github("https://github.com/school/labtrack.git", github_client())
    text = result["text"]
    assert result["name"] == "school/labtrack" and "items" in text and "laravel/framework" in text and "PHP" in text
    assert "TOPSECRETENV" not in text and ".env" not in text and "PEMSECRET" not in text and "NODEMODULE" not in text


def test_github_digest_hides_passwords_inside_code():
    assert repo_analysis.redact("'password' => 'SuperSecret123',") == "'password' => '[hidden]',"
    assert "abc123" not in repo_analysis.redact("API_KEY=abc123")


@pytest.mark.parametrize("link", ["https://evil.test/a/b", "file:///C:/x", "https://github.com/onlyowner", "https://github.com/a/b/../../c"])
def test_only_github_project_links_are_accepted(link):
    with pytest.raises(repo_analysis.RepoError):
        repo_analysis.from_github(link, github_client())


def test_private_or_missing_repository_is_explained():
    with pytest.raises(repo_analysis.RepoError, match="public"):
        repo_analysis.from_github("https://github.com/a/b", github_client(404, b""))


def test_local_folder_is_read_without_secrets(tmp_path):
    (tmp_path / "app.py").write_text("print('hello')", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("flask", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=TOPSECRET", encoding="utf-8")
    result = repo_analysis.from_folder(tmp_path)
    assert "flask" in result["text"] and "TOPSECRET" not in result["text"]


def test_code_parts_wait_for_a_repository_then_use_it(world, monkeypatch):  # noqa: F811
    client, web, relay = world
    start(client)
    result = write(client, "ch4.design.database")
    assert not result["ok"] and "code" in result["message"]
    monkeypatch.setattr(repo_analysis, "from_github", lambda _link: repo_analysis.from_zip("school/labtrack", repo_zip()))
    assert client.post("/api/repo/github", json={"link": "x"}).json()["ok"]
    relay.replies += [DRAFT, {"nodes": [{"id": "a", "label": "items"}], "edges": []}]
    assert write(client, "ch4.design.database")["ok"]
    assert "Schema::create" in relay.text_bodies()[0]["masked_input"]["repository"]
    assert web.state.session.diagram("ch4.design.database")["spec"]["nodes"][0]["label"] == "items"


# ---- results need real data --------------------------------------------------------------------


def test_test_cases_are_prepared_but_results_come_from_students(world):  # noqa: F811
    client, web, relay = world
    start(client)
    relay.replies.append({"rows": [{"id": "TC-01", "module": "Inventory", "scenario": "Add an item", "expected": "Saved", "actual": "INVENTED", "status": "Pass"}]})
    assert write(client, "ch4.testing", rows_only=True)["ok"]
    row = web.state.session.inputs("ch4.testing")["cases"][0]
    assert row == {"id": "TC-01", "module": "Inventory", "scenario": "Add an item", "expected": "Saved"}
    assert "Test cases" in write(client, "ch4.testing")["message"]
    row.update(actual="Saved", status="Pass")
    client.post("/api/part/ch4.testing/inputs", json={"cases": [row]})
    relay.replies.append(DRAFT)
    assert write(client, "ch4.testing")["ok"] and web.state.session.draft("ch4.testing")["after"][0]["rows"][0][5] == "Pass"


def test_preparing_again_never_overwrites_entered_results(world):  # noqa: F811
    client, web, relay = world
    start(client)
    client.post("/api/part/ch4.testing/inputs", json={"cases": [{"id": "T1", "scenario": "Real", "status": "Fail"}]})
    relay.replies.append({"rows": [{"id": "NEW", "scenario": "Replacement"}]})
    write(client, "ch4.testing", rows_only=True)
    assert web.state.session.inputs("ch4.testing")["cases"][0]["id"] == "T1"


def test_acceptability_waits_for_summarized_data_only(world):  # noqa: F811
    client, web, relay = world
    start(client)
    assert "Weighted means" in write(client, "ch4.acceptability")["message"]
    scores = g.inputs_for(web.state.session, "ch4.acceptability")["scores"]
    scores[0]["experts"] = "4.53"
    client.post("/api/part/ch4.acceptability/inputs", json={"scores": scores})
    relay.replies.append(DRAFT)
    assert write(client, "ch4.acceptability")["ok"]
    assert "4.53" in str(relay.text_bodies()[-1]["masked_input"]["tables"])


def test_chapter_5_waits_for_chapter_4(world):  # noqa: F811
    client, _web, relay = world
    start(client)
    assert not write(client, "ch5.summary")["ok"] and not relay.text_bodies()


# ---- references and appendices -----------------------------------------------------------------


def test_references_regenerate_in_apa_from_what_is_cited(world):  # noqa: F811
    client, web, _relay = world
    client.post("/api/part/ch2.sources/inputs", json={"sources": [{"authors": "Cruz, A.", "year": "2023", "title": "A study", "container": "J", "type": "Journal article"}]})
    client.get("/part/refs.list")
    assert not web.state.session.draft("refs.list")["after"]
    client.post("/api/part/ch1.scope/generate")
    client.post("/api/part/ch1.scope/draft", json={"html": "<p>Evidence (Cruz, 2023).</p>"})
    client.get("/part/refs.list")
    assert "Cruz, A. (2023)" in web.state.session.draft("refs.list")["after"][0]["html"]


@pytest.mark.parametrize("key", [p["key"] for p in spec_module.all_parts() if p["key"].startswith("app.")])
def test_every_appendix_can_be_marked_not_necessary(world, key):  # noqa: F811
    client, web, _relay = world
    assert client.post(f"/api/part/{key}/skip", json={"skip": True}).json()["ok"]
    assert key in g.disabled_parts(web.state.session)


def test_skipping_appendices_still_builds_a_valid_word_report(world):  # noqa: F811
    from tests.test_app_flow import fill_all

    client, web, _relay = world
    fill_all(client)
    for key in ("app.permission", "app.gantt", "app.manual", "app.cv"):
        client.post(f"/api/part/{key}/skip", json={"skip": True})
    assert client.post("/api/build", json={"final": False}).json()["ok"]


def test_appendices_can_be_auto_drafted(world):  # noqa: F811
    client, web, relay = world
    start(client)
    relay.replies.append(DRAFT)
    assert write(client, "app.permission")["ok"] and web.state.session.draft("app.permission")
