"""The local web app shown inside the desktop window."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shutil
import sqlite3
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import nh3
from fastapi import Body, FastAPI, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from app import computer_check, repo_analysis
from app.ai_client.client import AIError
from app.ai_client.service import AIService
from app.diagrams import KINDS, DiagramError, clean_spec, draw, starter
from app.docbuild import BuildError
from app.docbuild.standard import iso_characteristics
from app.parts import defense as defense_prep
from app.parts import disclosure
from app.parts import export as exporter
from app.parts import generators as g
from app.parts import spec as spec_module
from app.session import AppState, Session, SessionDamaged, SessionError, SUFFIX, data_dir, safety_copies, snapshot
from app.session.portable import PortableCopy, needs_local_copy
from app.sources import apa
from app.stats import evaluation
from app.uploads.processing import UploadError, process_upload

log = logging.getLogger("rrg")
BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
ALLOWED_TAGS = {"p", "strong", "b", "em", "i", "ol", "ul", "li", "h3", "br"}

TOKEN = secrets.token_urlsafe(24)
COOKIE = "rrg_token"
CHECK_TOKEN = os.environ.get("RRG_NO_TOKEN") != "1"
GATING = True

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(BASE / "app" / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "app" / "templates"))


def tagalog(text) -> Markup | str:
    """The small Tagalog line that goes under an English label."""
    translated = spec_module.translations().get(str(text))
    return Markup('<small class="tl" lang="tl">{}</small>').format(translated) if translated else ""


def bilingual(text) -> Markup:
    """Show an app message written as 'English || Tagalog'."""
    english, _, translated = str(text).partition(" || ")
    return Markup('{}<small class="tl" lang="tl">{}</small>').format(english, translated) if translated else Markup("{}").format(english)


templates.env.filters.update(tl=tagalog, bilingual=bilingual, tagalog_text=lambda text: spec_module.translations().get(str(text), ""))


class State:
    session: Session | None = None
    settings = AppState()
    last_export: Path | None = None
    pending_import = None
    portable: PortableCopy | None = None
    notice = ""


state = State()
ai = AIService(state.settings)


@app.middleware("http")
async def require_local_token(request: Request, call_next):
    if CHECK_TOKEN and request.url.path != "/enter" and request.cookies.get(COOKIE) != TOKEN:
        return HTMLResponse("Open this app from its own window.", status_code=403)
    return await call_next(request)


@app.get("/enter")
def enter(t: str = ""):
    if not secrets.compare_digest(t, TOKEN):
        return HTMLResponse("Open this app from its own window.", status_code=403)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(COOKIE, TOKEN, httponly=True, samesite="strict")
    return response


# ---- file dialogs ---------------------------------------------------------------------


def _window():
    try:
        import webview

        return webview.windows[0] if webview.windows else None
    except ImportError:
        return None


def pick_save(suggested: str, description: str, pattern: str) -> Path | None:
    window = _window()
    if window is None:  # automated tests run without a window
        folder = data_dir() / "exports"
        folder.mkdir(exist_ok=True)
        return folder / suggested
    import webview

    chosen = window.create_file_dialog(webview.FileDialog.SAVE, save_filename=suggested, file_types=(f"{description} ({pattern})",))
    if not chosen:
        return None
    return Path(chosen if isinstance(chosen, str) else chosen[0])


def pick_folder() -> Path | None:
    window = _window()
    if window is None:
        return None
    import webview

    chosen = window.create_file_dialog(webview.FileDialog.FOLDER)
    return Path(chosen[0]) if chosen else None


def pick_open() -> Path | None:
    window = _window()
    if window is None:
        return None
    import webview

    chosen = window.create_file_dialog(webview.FileDialog.OPEN, file_types=(f"Report sessions (*{SUFFIX})",))
    return Path(chosen[0]) if chosen else None


# ---- sessions -------------------------------------------------------------------------


def leave_session() -> str:
    """Write back to any USB drive, then close. Returns how the write-back went."""
    outcome = "none"
    if state.session:
        if state.portable:
            outcome = state.portable.write_back(state.session)
        state.session.close()
        state.session = None
    state.portable = None
    return outcome


def shutdown() -> None:
    leave_session()


def open_session(path: Path, create: bool = False) -> None:
    leave_session()
    portable = PortableCopy(path) if needs_local_copy(path) else None
    state.notice = portable.prepare(create) if portable else ""
    state.session = Session.create(portable.working if portable else path) if create else Session(portable.working if portable else path)
    state.portable = portable
    state.settings.remember(path)
    if create and portable:
        portable.write_back(state.session)
    if not create:
        try:
            snapshot(state.session)
        except (OSError, sqlite3.Error):  # a full disk must not stop a student opening their work
            log.exception("safety copy failed")


def first_part() -> str:
    return "/part/setup.school"


@app.get("/check-computer", response_class=HTMLResponse)
def check_computer(request: Request):
    return templates.TemplateResponse(request, "check.html", computer_check.run())


@app.get("/guide", response_class=HTMLResponse)
def student_guide(request: Request):
    return templates.TemplateResponse(request, "guide.html", {})


@app.get("/", response_class=HTMLResponse)
def home(request: Request, error: str = ""):
    if state.session:
        return RedirectResponse(state.session.get_meta("last_part") or first_part(), status_code=303)
    return templates.TemplateResponse(request, "launch.html", {"recent": state.settings.recent(), "error": error})


@app.post("/api/session/new")
def session_new():
    target = pick_save(f"Research Report{SUFFIX}", "Report sessions", f"*{SUFFIX}")
    if target is None:
        return {"ok": False}
    if target.suffix.lower() != SUFFIX:
        target = target.with_suffix(SUFFIX)
    if target.exists():
        target.unlink()
    open_session(target, create=True)
    return {"ok": True, "go": first_part()}


@app.post("/api/session/open")
def session_open(payload: dict = Body(default={})):
    path = Path(payload["path"]) if payload.get("path") else pick_open()
    if path is None:
        return {"ok": False}
    try:
        open_session(path)
    except SessionDamaged as exc:
        copies = [{"path": str(p), "when": datetime.fromtimestamp(p.stat().st_mtime).strftime("%B %d, %Y at %I:%M %p")} for p in safety_copies(path.stem)[:3]]
        message = f"{exc} Open your most recent safety copy below." if copies else f"{exc} There is no safety copy of it on this computer."
        return {"ok": False, "message": message, "safety": copies}
    except SessionError as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "go": "/"}


@app.post("/api/session/recover")
def session_recover(payload: dict = Body(...)):
    chosen = Path(str(payload.get("path", "")))
    folder = data_dir() / "safety-copies"
    if chosen.resolve().parent != folder.resolve() or not chosen.exists():
        return {"ok": False, "message": "This safety copy could not be found."}
    recovered = data_dir() / "recovered" / chosen.name
    recovered.parent.mkdir(exist_ok=True)
    shutil.copy2(chosen, recovered)
    open_session(recovered)
    state.notice = "This is your safety copy. Choose File, then Save a copy as, to keep it where you want it."
    return {"ok": True, "go": "/"}


@app.get("/api/session/sync")
def session_sync():
    if not state.session or not state.portable:
        return {"state": "none"}
    return {"state": state.portable.write_back(state.session)}


@app.post("/api/session/save-as")
def session_save_as():
    session = state.session
    if not session:
        return {"ok": False}
    target = pick_save(session.path.name, "Report sessions", f"*{SUFFIX}")
    if target is None:
        return {"ok": False}
    if target.suffix.lower() != SUFFIX:
        target = target.with_suffix(SUFFIX)
    if target.resolve() == (state.portable.origin if state.portable else session.path).resolve():
        return {"ok": True, "message": "Your session is already saved here."}
    session.copy_to(target)
    open_session(target)
    return {"ok": True, "message": f"Saved as {target.name}. You are now working in this copy."}


@app.post("/api/session/close")
def session_close():
    outcome = leave_session()
    if outcome in ("missing", "failed"):
        return {"ok": True, "go": "/?error=" + quote("Your report could not reach the drive, but it is safe on this computer. Connect the drive and open the report again.")}
    return {"ok": True, "go": "/"}


# ---- part pages -----------------------------------------------------------------------


def tab_view(session: Session) -> list[dict]:
    status = session.status()
    disabled = g.disabled_parts(session)
    open_notes = session.open_note_counts()
    skipped = g.not_necessary(session)
    locked = g.locks(session) if GATING else {}
    output = []
    for tab in spec_module.tabs():
        parts = []
        for part in tab["parts"]:
            if part["key"] in disabled and part["key"] not in skipped:
                continue
            parts.append({
                "key": part["key"], "label": part["label"], "status": "skipped" if part["key"] in skipped else status.get(part["key"], "empty"),
                "notes": open_notes.get(part["key"], 0), "locked": part["key"] in locked,
            })
        gated = [p for p in parts if not (spec_module.part(p["key"]) or {}).get("no_output")]
        output.append({"id": tab["id"], "label": tab["label"], "parts": parts, "locked": bool(gated) and all(p["locked"] for p in gated)})
    return output


CHAPTER_TABS = ("prelim", "ch1", "ch2", "ch3", "ch4", "ch5", "refs", "app")


def built_chapters(session: Session) -> set[str]:
    return set(json.loads(session.get_meta("master_chapters") or "[]"))


def unfinished(session: Session, tab_id: str) -> list[str]:
    """Parts of a gated chapter that are neither approved nor marked not necessary."""
    if tab_id not in g.GATED_TABS:
        return []
    status, disabled = session.status(), g.disabled_parts(session)
    tab = next(t for t in spec_module.tabs() if t["id"] == tab_id)
    return [p["label"] for p in tab["parts"] if not p.get("no_output") and p["key"] not in disabled and status.get(p["key"]) != "reviewed"]


def chapter_view(session: Session, tab_id: str) -> dict | None:
    if tab_id not in CHAPTER_TABS:
        return None
    missing = unfinished(session, tab_id)
    return {"id": tab_id, "ready": not missing, "missing": len(missing), "built": tab_id in built_chapters(session), "instrument": tab_id == "ch3" and not missing}


def choice_options(key: str, values: dict) -> list[dict]:
    auto = spec_module.auto_config(key) or {}
    if auto.get("choices"):
        return [{"name": name} for name in auto["choices"]]
    return json.loads(values.get("options_json") or "[]")


def master_cache(session: Session) -> Path:
    folder = data_dir() / "masters"
    folder.mkdir(exist_ok=True)
    return folder / f"{hashlib.sha256(str(session.path).encode('utf-8')).hexdigest()[:16]}.docx"


def pop_notice() -> str:
    notice, state.notice = state.notice, ""
    return notice


def diagram_view(session: Session, key: str, part: dict) -> dict | None:
    kinds = (part.get("attach") or {}).get("diagram")
    if not kinds:
        return None
    saved = session.diagram(key) or {}
    kind = saved["kind"] if saved.get("kind") in kinds else kinds[0]
    rendered = saved.get("rendered") if any(f["id"] == saved.get("rendered") for f in session.attachments(key)) else None
    return {
        "kinds": {k: KINDS[k] for k in kinds}, "kind": kind,
        "spec": saved.get("spec") or starter(kind, g.inputs_for(session, key), g.inputs_for(session, "setup.system")),
        "rendered": rendered, "stale": bool(rendered and saved.get("drawn") != saved.get("spec")),
        "proposed": bool(saved.get("event")), "missing_info": saved.get("missing_info") or [],
    }


def disclosure_view(session: Session) -> dict:
    return {"text": disclosure.statement(session), "confirmed": disclosure.is_confirmed(session), "log": disclosure.log_rows(session)}


def block_previews(draft: dict | None) -> tuple[list, list]:
    if not draft:
        return [], []
    return draft.get("before", []), draft.get("after", [])


@app.get("/part/{key}", response_class=HTMLResponse)
def part_page(request: Request, key: str):
    session = state.session
    if not session:
        return RedirectResponse("/", status_code=303)
    part = spec_module.part(key)
    if part is None:
        return RedirectResponse(first_part(), status_code=303)
    session.set_meta("last_part", f"/part/{key}")
    tabs = tab_view(session)
    flat = [p["key"] for tab in tabs for p in tab["parts"]]
    position = flat.index(key) if key in flat else 0
    current_tab = next((t for t in tabs if t["id"] == part["tab"]), tabs[0])
    draft = session.draft(key)
    before, after = block_previews(draft)
    waiting_for = g.locks(session).get(key) if GATING else None
    if part.get("auto_build") and not waiting_for and key not in g.not_necessary(session):
        fresh = g.generate(session, key)
        existing = session.draft(key)
        if not existing or (key == "refs.list" and existing.get("after") != fresh.get("after")):
            session.save_draft(key, fresh, "assembled")
            draft = session.draft(key)
            before, after = block_previews(draft)
    context = {
        "lock": spec_module.part(waiting_for) if waiting_for else None, "skipped": key in g.not_necessary(session),
        "tabs": tabs, "tab": current_tab, "part": part, "values": g.inputs_for(session, key),
        "guidance": spec_module.guidance_for(key), "draft": draft, "before": before, "after": after,
        "status": session.status().get(key, "empty"), "versions": session.version_count(key),
        "previous": flat[position - 1] if position else None,
        "next": flat[position + 1] if position + 1 < len(flat) else None,
        "labels": {p["key"]: p["label"] for t in tabs for p in t["parts"]},
        "session_name": session.path.stem, "iso_names": [n for n, _, _ in iso_characteristics(g.iso_edition(session))],
        "facts": g.build_facts(session), "disabled": g.disabled_parts(session),
        "has_editor": not (part.get("setup") or part.get("auto") or part.get("view") or part.get("no_output")),
        "attachments": session.attachments(key) if part.get("attach") else [],
        "chapter": chapter_view(session, current_tab["id"]),
        "repo": json.loads(session.get_meta("repo_digest") or "null"),
        "choice_options": choice_options(key, g.inputs_for(session, key)),
        "source_count": len(g.sources_of(session)),
        "used_sources": [s for s in g.sources_of(session) if key in (s.get("used_for") or "").split(",")],
        "portable": bool(state.portable), "notice": pop_notice(),
        "notes": session.notes(key) if not (part.get("setup") or part.get("view")) else None,
        "disclosure": disclosure_view(session) if key == "ch3.ethics" else None,
        "defense_issues": json.loads(session.get_meta("defense_issues") or "[]") if part.get("defense") else [],
        "ai": ai.page_info(session, key), "ai_available": ai.client.configured, "diagram": diagram_view(session, key, part),
    }
    if part.get("view") in ("check", "checklist", "build"):
        context["issues"] = exporter.run_checks(session)
        context["blocking"] = [i for i in context["issues"] if i["blocking"]]
        context["checklist"] = exporter.CHECKLIST
        context["checked"] = g.inputs_for(session, "finish.checklist").get("done", [])
    return templates.TemplateResponse(request, "part.html", context)


def active() -> Session:
    if not state.session:
        raise SessionError("Open a session first.")
    return state.session


@app.exception_handler(sqlite3.Error)
def storage_error(_request: Request, exc: sqlite3.Error):
    log.error("storage error: %s", type(exc).__name__)
    return JSONResponse({"ok": False, "message": "Your last change could not be saved. Check that this computer has free disk space, then try again."}, status_code=500)


@app.exception_handler(SessionError)
def session_error(_request: Request, exc: SessionError):
    return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)


@app.post("/api/part/{key}/inputs")
def save_inputs(key: str, data: dict = Body(...)):
    session = active()
    part = spec_module.part(key)
    for field in (part or {}).get("inputs", []):
        if field["type"] == "repeater":
            for index, row in enumerate(data.get(field["id"]) or [], start=1):
                if "rq_id" in row and not row["rq_id"]:
                    row["rq_id"] = f"q{secrets.token_hex(3)}"
    session.save_inputs(key, data)
    return {"ok": True, "ai_blocked": ai.page_info(session, key)["blocked"]}


@app.post("/api/part/{key}/generate")
def generate_part(key: str):
    session = active()
    session.save_draft(key, g.generate(session, key), "assembled")
    return {"ok": True}


@app.post("/api/part/{key}/draft")
def save_draft(key: str, payload: dict = Body(...)):
    session = active()
    draft = session.draft(key) or {"before": [], "after": []}
    draft["html"] = nh3.clean(payload.get("html", ""), tags=ALLOWED_TAGS, attributes={})
    session.save_draft(key, draft, "edited", version=False)
    if draft.get("ai"):
        session.set_ai_outcome(draft["ai"]["event"], "edited")
        draft["ai"]["issues"] = ai.rule_issues(session, key)
        session.save_draft(key, draft, "edited", version=False)
    return {"ok": True, "ai_issues": (draft.get("ai") or {}).get("issues")}


@app.post("/api/chapter/{tab_id}/build")
def build_chapter(tab_id: str):
    session = active()
    if tab_id not in CHAPTER_TABS:
        return {"ok": False, "message": "This tab has no chapter to build."}
    missing = unfinished(session, tab_id)
    if missing:
        return {"ok": False, "message": "Approve every part first: " + ", ".join(missing) + ". || Aprubahan muna ang lahat ng bahagi."}
    tab = next(t for t in spec_module.tabs() if t["id"] == tab_id)
    for part in tab["parts"]:
        if part.get("auto_build") and part["key"] not in g.disabled_parts(session):
            session.save_draft(part["key"], g.generate(session, part["key"]), "assembled", version=False)
    built = built_chapters(session) | {tab_id}
    try:
        data = exporter.master_report(session, built)
    except BuildError as exc:
        return {"ok": False, "message": str(exc)}
    session.set_meta("master_chapters", json.dumps(sorted(built)))
    master_cache(session).write_bytes(data)
    home = (state.portable.origin if state.portable else session.path).parent
    target = home / f"{session.path.stem} Master.docx"
    try:
        target.write_bytes(data)
        return {"ok": True, "message": f"Added to {target.name}. || Naidagdag sa Word master."}
    except OSError:
        return {"ok": True, "message": "The preview is updated. Close the Word master so it can be saved too. || Na-update ang preview. Isara muna ang Word master para ma-save ito."}


@app.get("/api/master-preview")
def master_preview():
    cache = master_cache(active())
    if not cache.exists():
        return Response(status_code=204)
    return Response(cache.read_bytes(), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers={"Cache-Control": "no-store"})


@app.post("/api/instrument/download")
def download_instrument():
    session = active()
    if unfinished(session, "ch3"):
        return {"ok": False, "message": "Finish Chapter 3 first. || Tapusin muna ang Chapter 3."}
    return write_export(exporter.instrument_document(session), "Research Instrument.docx")


@app.post("/api/repo/github")
def repo_github(payload: dict = Body(...)):
    try:
        return store_repo(repo_analysis.from_github(str(payload.get("link", ""))))
    except repo_analysis.RepoError as exc:
        return {"ok": False, "message": str(exc)}


@app.post("/api/repo/folder")
def repo_folder():
    folder = pick_folder()
    if folder is None:
        return {"ok": False}
    try:
        return store_repo(repo_analysis.from_folder(folder))
    except repo_analysis.RepoError as exc:
        return {"ok": False, "message": str(exc)}


def store_repo(result: dict) -> dict:
    active().set_meta("repo_digest", json.dumps(result, ensure_ascii=False))
    return {"ok": True, "message": f"Read {result['name']}: {result['files']} files. || Nabasa na ang code."}


@app.post("/api/part/{key}/skip")
def skip_part(key: str, payload: dict = Body(...)):
    if not (spec_module.part(key) or {}).get("optional"):
        return {"ok": False, "message": "This part is required."}
    g.set_not_necessary(active(), key, bool(payload.get("skip")))
    return {"ok": True}


@app.post("/api/part/{key}/review")
def review_part(key: str, payload: dict = Body(...)):
    session = active()
    if payload.get("reviewed") and "[KULANG" in ((session.draft(key) or {}).get("html") or ""):
        return {"ok": False, "message": "Fill in every [KULANG: ...] first. || May natitira pang [KULANG: ...] sa teksto. Punan muna ang mga ito."}
    session.set_reviewed(key, bool(payload.get("reviewed")))
    event = ((session.draft(key) or {}).get("ai") or {}).get("event")
    if event and payload.get("reviewed"):
        session.set_ai_outcome(event, "accepted")
    return {"ok": True}


# ---- built-in AI ----------------------------------------------------------------------


def ai_action(action) -> dict:
    """Run an AI action. Any failure leaves the student's saved work exactly as it was."""
    try:
        return {"ok": True, **action()}
    except AIError as exc:
        return {"ok": False, "message": str(exc)}


def diagram_kind(key: str, requested) -> str:
    kinds = ((spec_module.part(key) or {}).get("attach") or {}).get("diagram") or []
    return requested if requested in kinds else (kinds[0] if kinds else "")


@app.post("/api/part/{key}/diagram")
def save_diagram(key: str, payload: dict = Body(...)):
    session = active()
    kind = diagram_kind(key, payload.get("kind"))
    if not kind:
        return {"ok": False, "message": "This part has no diagram."}
    saved = session.diagram(key) or {}
    keep = {k: saved[k] for k in ("drawn", "event", "proposed", "missing_info") if k in saved}
    session.save_diagram(key, kind, {**keep, "spec": clean_spec(kind, payload.get("spec"))}, saved.get("rendered"))
    return {"ok": True}


@app.post("/api/part/{key}/diagram/start")
def start_diagram(key: str, payload: dict = Body(...)):
    session = active()
    kind = diagram_kind(key, payload.get("kind"))
    if not kind:
        return {"ok": False, "message": "This part has no diagram."}
    return {"ok": True, "spec": starter(kind, g.inputs_for(session, key), g.inputs_for(session, "setup.system"))}


@app.post("/api/part/{key}/diagram/draw")
def draw_diagram(key: str, payload: dict = Body(...)):
    session = active()
    kind = diagram_kind(key, payload.get("kind"))
    if not kind:
        return {"ok": False, "message": "This part has no diagram."}
    try:
        result = draw(kind, payload.get("spec") or {}, g.inputs_for(session, "setup.system").get("system_name") or "System")
    except DiagramError as exc:
        return {"ok": False, "message": str(exc)}
    spec = clean_spec(kind, payload.get("spec"))
    saved = session.diagram(key) or {}
    if saved.get("rendered"):
        session.delete_attachment(saved["rendered"])
    rendered = session.add_attachment(key, "diagram", f"{KINDS[kind]['name']}.png", "image/png", result.png, first=True)
    keep = {k: saved[k] for k in ("event", "proposed", "missing_info") if k in saved}
    session.save_diagram(key, kind, {**keep, "spec": spec, "drawn": spec}, rendered)
    if saved.get("event"):
        session.set_ai_outcome(saved["event"], "accepted" if spec == saved.get("proposed") else "edited")
    return {"ok": True, "warnings": result.warnings}


@app.post("/api/part/{key}/ai/diagram")
def ai_diagram(key: str, payload: dict = Body(...)):
    def action() -> dict:
        session = active()
        kind = diagram_kind(key, payload.get("kind"))
        store_proposal(session, key, kind)
        return {}
    return ai_action(action)


@app.post("/api/part/{key}/ai/options")
def ai_options(key: str):
    return ai_action(lambda: ai.recommend_options(active(), key))


def store_proposal(session: Session, key: str, kind: str) -> None:
    proposal = ai.propose_diagram(session, key, kind)
    saved = session.diagram(key) or {}
    session.save_diagram(key, kind, {
        "spec": proposal["spec"], "proposed": proposal["spec"], "event": proposal["event"],
        "missing_info": proposal["missing_info"], **({"drawn": saved["drawn"]} if "drawn" in saved else {}),
    }, saved.get("rendered"))


@app.post("/api/part/{key}/ai/synthesize")
def ai_synthesize(key: str, payload: dict = Body(default={})):
    def action() -> dict:
        session = active()
        rows_only = bool(payload.get("rows_only"))
        result = ai.synthesize(session, key, rows_only)
        kinds = ((spec_module.part(key) or {}).get("attach") or {}).get("diagram")
        if kinds and not rows_only and (spec_module.auto_config(key) or {}).get("repo"):
            try:
                store_proposal(session, key, kinds[0])
            except AIError:
                log.info("diagram proposal skipped")
        return result
    return ai_action(action)


@app.post("/api/part/{key}/refresh-tables")
def refresh_tables(key: str):
    """Rebuild a part's tables from the answers without touching the edited text."""
    session = active()
    draft = session.draft(key)
    if not draft:
        return {"ok": False, "message": "Build this part first."}
    fresh = g.generate(session, key)
    draft.update(before=fresh.get("before", []), after=fresh.get("after", []))
    session.save_draft(key, draft, "edited", version=False)
    return {"ok": True}


@app.post("/api/part/{key}/ai/topics")
def ai_topics(key: str):
    return ai_action(lambda: ai.recommend_topics(active(), key))


@app.post("/api/part/{key}/ai/research")
def ai_research(key: str):
    return ai_action(lambda: ai.research(active(), key))


@app.post("/api/part/{key}/ai/guided")
def ai_guided(key: str):
    return ai_action(lambda: ai.guided(active(), key))


@app.post("/api/part/{key}/ai/defense")
def ai_defense(key: str):
    return ai_action(lambda: ai.defense(active(), key))


@app.post("/api/part/{key}/ai/image")
def ai_image(key: str, payload: dict = Body(...)):
    return ai_action(lambda: ai.create_image(active(), key, str(payload.get("description", ""))))


@app.post("/api/attachment/{attachment_id}/keep")
def keep_image(attachment_id: int):
    session = active()
    info = session.attachment_info(attachment_id)
    if not info or info["kind"] != "ai_option" or (spec_module.ai_config(info["part_key"]) or {}).get("images") != "mockup":
        return {"ok": False, "message": "This image cannot be placed in the report."}
    session.keep_option(attachment_id)
    return {"ok": True}


@app.post("/api/attachment/{attachment_id}/save-file")
def save_image_file(attachment_id: int):
    session = active()
    info = session.attachment_info(attachment_id)
    if not info or not info["ai"]:
        return {"ok": False, "message": "This image cannot be saved."}
    target = pick_save("Slide background.png" if info["part_key"] == "finish.defense" else "System logo.png", "PNG images", "*.png")
    if target is None:
        return {"ok": False}
    try:
        target.write_bytes(session.attachment_data(attachment_id)[0])
    except OSError:
        return {"ok": False, "message": "The image could not be saved there."}
    return {"ok": True, "message": f"Saved {target.name}."}


@app.post("/api/open-update")
def open_update():
    if ai.update_url:
        webbrowser.open(ai.update_url)
        return {"ok": True}
    return {"ok": False, "message": "No update is available."}


@app.get("/api/ai/status")
def ai_status():
    return ai.status()


@app.post("/api/part/{key}/ai/write")
def ai_write(key: str):
    return ai_action(lambda: ai.write(active(), key))


@app.post("/api/part/{key}/ai/suggest")
def ai_suggest(key: str, payload: dict = Body(...)):
    return ai_action(lambda: ai.suggest(active(), key, str(payload.get("field", ""))))


@app.post("/api/part/{key}/ai/check")
def ai_check(key: str):
    return ai_action(lambda: ai.check(active(), key))


@app.post("/api/part/{key}/restore")
def restore_part(key: str):
    return {"ok": active().restore_previous(key)}


@app.post("/api/part/{key}/notes")
def add_note(key: str, payload: dict = Body(...)):
    author = str(payload.get("author", "")).strip()[:80]
    body = str(payload.get("body", "")).strip()[:2000]
    if not author or not body:
        return {"ok": False, "message": "Type your name and your note."}
    active().add_note(key, author, body)
    return {"ok": True}


@app.post("/api/note/{note_id}")
def resolve_note(note_id: int, payload: dict = Body(...)):
    active().set_note_resolved(note_id, bool(payload.get("resolved")))
    return {"ok": True}


@app.post("/api/disclosure")
def confirm_disclosure(payload: dict = Body(...)):
    disclosure.confirm(active(), bool(payload.get("confirmed")))
    return {"ok": True}


@app.post("/api/defense/fill")
def fill_defense():
    session = active()
    session.save_inputs("finish.defense", defense_prep.outline(session))
    session.set_meta("defense_issues", "[]")
    return {"ok": True}


@app.post("/api/defense/download")
def download_defense():
    return write_export(defense_prep.to_docx(active()), "Defense Prep.docx")


@app.post("/api/part/{key}/attachments")
async def upload_files(key: str, files: list[UploadFile]):
    session = active()
    config = (spec_module.part(key) or {}).get("attach")
    if not config:
        return JSONResponse({"ok": False, "message": "This part does not accept files."}, status_code=400)
    added, problems = 0, []
    for upload in files:
        raw = await upload.read()
        try:
            for item in process_upload(raw, upload.filename or "file"):
                session.add_attachment(key, "file", item.filename, item.mime, item.data)
                added += 1
        except UploadError as exc:
            problems.append(str(exc))
    return {"ok": added > 0, "added": added, "message": " ".join(problems)}


@app.get("/attachment/{attachment_id}")
def attachment_file(attachment_id: int):
    found = active().attachment_data(attachment_id)
    if not found:
        return Response(status_code=404)
    return Response(content=found[0], media_type=found[1], headers={"Cache-Control": "no-store"})


@app.post("/api/attachment/{attachment_id}")
def update_attachment(attachment_id: int, payload: dict = Body(...)):
    active().update_attachment(attachment_id, str(payload.get("caption", "")).strip(), bool(payload.get("consent")))
    return {"ok": True}


@app.post("/api/attachment/{attachment_id}/delete")
def delete_attachment(attachment_id: int):
    active().delete_attachment(attachment_id)
    return {"ok": True}


# ---- evaluation data ------------------------------------------------------------------


@app.post("/api/import/pilot")
async def import_pilot(file: UploadFile):
    session = active()
    try:
        result = evaluation.pilot_alpha(evaluation.read_table(await file.read(), file.filename or ""))
    except evaluation.ImportError_ as exc:
        return {"ok": False, "message": str(exc)}
    inputs = g.inputs_for(session, "ch3.instrument")
    inputs["alpha"] = f"{result['alpha']:.2f}"
    session.save_inputs("ch3.instrument", inputs)
    verdict = "acceptable" if result["alpha"] >= 0.70 else "below the acceptable level of 0.70"
    return {"ok": True, "message": f"Cronbach's alpha is {result['alpha']:.2f} from {result['respondents']} respondents and {result['items']} questions, which is {verdict}. Build this part again to include it."}


@app.post("/api/import/evaluation")
async def import_evaluation(file: UploadFile):
    session = active()
    characteristics = [name for name, _, _ in iso_characteristics(g.iso_edition(session))]
    try:
        frame = evaluation.read_table(await file.read(), file.filename or "")
    except evaluation.ImportError_ as exc:
        return {"ok": False, "message": str(exc)}
    state.pending_import = frame  # held in memory only, never written to the session
    columns = [str(c) for c in frame.columns]
    return {"ok": True, "columns": columns, "guess": evaluation.guess_mapping(columns, characteristics),
            "characteristics": characteristics, "rows": len(frame)}


@app.post("/api/import/evaluation/apply")
def apply_evaluation(payload: dict = Body(...)):
    session = active()
    frame = state.pending_import
    if frame is None:
        return {"ok": False, "message": "Import the file again."}
    mapping = payload.get("mapping") or {}
    try:
        cleaned = evaluation.clean(frame, mapping)
    except evaluation.ImportError_ as exc:
        return {"ok": False, "message": str(exc)}
    state.pending_import = None
    used = {c: t for c, t in mapping.items() if t and t != "__group__"}
    session.save_dataset("evaluation", {"characteristics": sorted(set(used.values()))}, cleaned)

    results = evaluation.weighted_means(cleaned)
    inputs = g.inputs_for(session, "ch4.acceptability")
    for row in inputs["scores"]:
        name = row["characteristic"]
        for group in ("experts", "users"):
            value = results["means"][group].get(name)
            row[group] = f"{value:.2f}" if value is not None else ""
    session.save_inputs("ch4.acceptability", inputs)

    message = f"Imported {results['counts']['experts']} IT experts and {results['counts']['users']} end-users."
    if cleaned["unreadable_answers"]:
        message += f" {cleaned['unreadable_answers']} answers could not be read and were skipped."
    comparison = evaluation.compare_groups(cleaned)
    if comparison:
        difference = g.inputs_for(session, "ch4.difference")
        difference.update({k: comparison[k] for k in ("experts_mean", "experts_sd", "users_mean", "users_sd", "t", "p")})
        session.save_inputs("ch4.difference", difference)
        if "mann_whitney_p" in comparison:
            message += f" A group has fewer than 10 people. Ask your adviser about Mann-Whitney U (p = {comparison['mann_whitney_p']})."
    return {"ok": True, "message": message}


@app.post("/api/sources/search")
def source_search(payload: dict = Body(...)):
    query = str(payload.get("query", ""))[:200]
    try:
        return {"ok": True, "results": apa.search_studies(query)}
    except apa.LookupError_:  # a school sharing one address can use up the keyless daily budget
        try:
            return {"ok": True, "results": apa.lookup(query)}
        except apa.LookupError_ as exc:
            return {"ok": False, "message": str(exc)}


@app.post("/api/sources/add")
def source_add(payload: dict = Body(...)):
    session = active()
    allowed = ("authors", "year", "title", "container", "volume", "issue", "pages", "link", "type", "origin", "notes")
    source = {name: str((payload.get("source") or {}).get(name) or "")[:2000] for name in allowed}
    if not source["title"].strip():
        return {"ok": False, "message": "This source has no title."}
    values = g.inputs_for(session, "ch2.sources")
    sources = values.setdefault("sources", [])
    if not any((s.get("title") or "").strip().lower() == source["title"].strip().lower() for s in sources):
        sources.append(source)
        session.save_inputs("ch2.sources", values)
    return {"ok": True, "count": len(g.sources_of(session))}


@app.post("/api/sources/remove")
def source_remove(payload: dict = Body(...)):
    session = active()
    title = str(payload.get("title", "")).strip().lower()
    values = g.inputs_for(session, "ch2.sources")
    values["sources"] = [s for s in values.get("sources", []) if (s.get("title") or "").strip().lower() != title]
    session.save_inputs("ch2.sources", values)
    return {"ok": True}


@app.post("/api/sources/lookup")
def source_lookup(payload: dict = Body(...)):
    try:
        return {"ok": True, "results": apa.lookup(str(payload.get("query", "")))}
    except apa.LookupError_ as exc:
        return {"ok": False, "message": str(exc)}


def write_export(data: bytes, suggested: str) -> dict:
    target = pick_save(suggested, "Word documents", "*.docx")
    if target is None:
        return {"ok": False}
    if target.suffix.lower() != ".docx":
        target = target.with_suffix(".docx")
    try:
        target.write_bytes(data)
    except PermissionError:
        return {"ok": False, "message": "Close this file in Word, then try again."}
    state.last_export = target
    return {"ok": True, "message": f"Saved {target.name}.", "can_open": True}


@app.post("/api/part/{key}/download")
def download_part(key: str):
    session = active()
    part = spec_module.part(key)
    try:
        data = exporter.export_part(session, key)
    except BuildError as exc:
        return {"ok": False, "message": str(exc)}
    return write_export(data, f"{part['label']}.docx")


@app.post("/api/build")
def build_full(payload: dict = Body(...)):
    session = active()
    final = bool(payload.get("final"))
    if final:
        blocking = [i for i in exporter.run_checks(session) if i["blocking"]]
        if blocking:
            return {"ok": False, "message": f"Resolve {len(blocking)} item{'s' if len(blocking) != 1 else ''} in the Consistency Check first."}
    try:
        result = exporter.export_report(session, final)
    except BuildError as exc:
        return {"ok": False, "message": str(exc)}
    name = session.path.stem + (" Final.docx" if final else " Draft.docx")
    return write_export(result.data, name)


@app.post("/api/open-export")
def open_export():
    if state.last_export and state.last_export.exists():
        os.startfile(state.last_export)  # noqa: S606 - opens a file the user just saved
        return {"ok": True}
    return {"ok": False, "message": "The saved file could not be found."}
