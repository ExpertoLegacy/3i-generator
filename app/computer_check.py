"""Check whether this computer can run the app well. Reports nothing about students or their work."""
from __future__ import annotations

import ctypes
import os
import shutil
import sqlite3
import sys
import tempfile
import winreg
from pathlib import Path

import httpx
from docx import Document

from app import diagrams, net
from app.ai_client.client import relay_url
from app.docbuild import builder
from app.session import data_dir
from app.version import VERSION

OK, WARN, PROBLEM = "ok", "warn", "problem"
MINIMUM_BUILD = 17763  # Windows 10 version 1809
WEBVIEW2_KEY = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def _registry_value(hive, path: str, name: str) -> str:
    try:
        with winreg.OpenKey(hive, path) as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return ""


def windows() -> dict:
    build = sys.getwindowsversion().build
    if build < MINIMUM_BUILD:
        return {"name": "Windows", "status": PROBLEM, "detail": f"Build {build} is too old. The app needs Windows 10 version 1809 or newer."}
    return {"name": "Windows", "status": OK, "detail": f"{'Windows 11' if build >= 22000 else 'Windows 10'}, build {build}"}


def webview2() -> dict:
    version = (_registry_value(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\{WEBVIEW2_KEY}", "pv")
               or _registry_value(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\{WEBVIEW2_KEY}", "pv")
               or _registry_value(winreg.HKEY_CURRENT_USER, rf"Software\{WEBVIEW2_KEY}", "pv"))
    if version and version != "0.0.0.0":
        return {"name": "App window (WebView2)", "status": OK, "detail": f"Version {version}"}
    return {"name": "App window (WebView2)", "status": WARN, "detail": "Not listed in the usual place. If the app window opens normally, you can ignore this."}


def word() -> dict:
    current = _registry_value(winreg.HKEY_CLASSES_ROOT, r"Word.Application\CurVer", "")
    if current:
        return {"name": "Microsoft Word", "status": OK, "detail": "Installed"}
    return {"name": "Microsoft Word", "status": WARN, "detail": "Not found. The app still makes reports, but open them in Word on another computer to update the table of contents and page numbers. WPS Office and LibreOffice may show them differently."}


def report_font() -> dict:
    folders = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts", Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]
    if any(folder.is_dir() and any(folder.glob("BOOK*.TT*")) for folder in folders):
        return {"name": "Report font (Bookman Old Style)", "status": OK, "detail": "Installed"}
    return {"name": "Report font (Bookman Old Style)", "status": WARN, "detail": "Not installed. It comes with Microsoft Office, and Microsoft 365 can download it. Without it, Word shows a substitute font and page breaks may shift, so print from a computer that has it."}


def disk() -> dict:
    free = shutil.disk_usage(data_dir()).free / 1024 ** 3
    if free < 0.5:
        return {"name": "Free disk space", "status": PROBLEM, "detail": f"Only {free:.1f} GB free. Free up space before working."}
    return {"name": "Free disk space", "status": WARN if free < 2 else OK, "detail": f"{free:.1f} GB free"}


def memory() -> dict:
    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong), ("available", ctypes.c_ulonglong),
                    ("page_total", ctypes.c_ulonglong), ("page_free", ctypes.c_ulonglong), ("virtual_total", ctypes.c_ulonglong),
                    ("virtual_free", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]

    status = Status()
    status.length = ctypes.sizeof(Status)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    total = status.total / 1024 ** 3
    return {"name": "Memory", "status": WARN if total < 3.5 else OK, "detail": f"{total:.0f} GB" + (". The app may be slow. Close other programs while working." if total < 3.5 else "")}


def saving() -> dict:
    try:
        with tempfile.TemporaryDirectory(dir=data_dir()) as folder:
            db = sqlite3.connect(Path(folder) / "check.rrg")
            db.execute("CREATE TABLE t(a)")
            db.commit()
            db.close()
    except (OSError, sqlite3.Error):
        return {"name": "Saving reports", "status": PROBLEM, "detail": f"The app cannot write to {data_dir()}. A security program or school restriction may be blocking it."}
    return {"name": "Saving reports", "status": OK, "detail": "Working"}


def diagram_drawing() -> dict:
    spec = {"nodes": [{"id": "a", "label": "Check", "type": "Actor"}, {"id": "b", "label": "Draw a diagram", "type": "Use case"}], "edges": [{"from": "a", "to": "b"}]}
    try:
        diagrams.draw("usecase", spec, "Computer check")
    except diagrams.DiagramError:
        return {"name": "Drawing diagrams", "status": PROBLEM, "detail": "The drawing tool could not run. A security program may be blocking it."}
    return {"name": "Drawing diagrams", "status": OK, "detail": "Working"}


def word_template() -> dict:
    try:
        Document(builder.MASTER)
    except Exception:  # noqa: BLE001 - any failure here means the installation is incomplete
        return {"name": "Report template", "status": PROBLEM, "detail": "The report template is missing or damaged. Reinstall the app."}
    return {"name": "Report template", "status": OK, "detail": "Found"}


def ai_connection(client: httpx.Client | None = None) -> dict:
    address = relay_url()
    if not address:
        return {"name": "AI connection", "status": WARN, "detail": "AI is not set up in this copy."}
    owned = client is None
    client = client or httpx.Client(transport=net.transport(), timeout=httpx.Timeout(12, connect=8))
    try:
        response = client.get(f"{address}/v1/health")
        if response.status_code == 200 and response.json().get("ok"):
            return {"name": "AI connection", "status": OK, "detail": "Connected"}
        return {"name": "AI connection", "status": WARN, "detail": f"The network answered with an error ({response.status_code}). It may block the AI service. Everything else keeps working."}
    except (httpx.HTTPError, ValueError) as exc:
        return {"name": "AI connection", "status": WARN, "detail": f"{net.explain(exc)} Everything else keeps working."}
    finally:
        if owned:
            client.close()


CHECKS = [windows, webview2, memory, disk, saving, word_template, diagram_drawing, word, report_font, ai_connection]


def run() -> dict:
    results = []
    for check in CHECKS:
        try:
            results.append(check())
        except Exception as exc:  # noqa: BLE001 - one failed check must not hide the others
            results.append({"name": check.__name__, "status": WARN, "detail": f"Could not be checked ({type(exc).__name__})."})
    problems = sum(r["status"] == PROBLEM for r in results)
    warnings = sum(r["status"] == WARN for r in results)
    if problems:
        summary = "This computer has a problem that will stop the app from working properly."
    elif warnings:
        summary = "This computer can run the app. Read the notes below."
    else:
        summary = "This computer is ready."
    return {"results": results, "summary": summary, "problems": problems, "version": VERSION}
