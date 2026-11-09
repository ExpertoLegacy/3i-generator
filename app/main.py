"""Desktop entry point: local server on a private port, shown in an app window."""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn
import webview

from app import runtime_log
from app.runtime_log import log
from app.web import TOKEN, ai, app, shutdown, state


def window_size(screen_width: int, screen_height: int) -> tuple[int, int]:
    """Fit small laptops: the taskbar and window frame must stay on screen."""
    return min(1360, screen_width - 40), min(860, screen_height - 80)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _exercise_packaged_features() -> dict:
    """Run real features inside the frozen build, where bundled files are most likely to be missing."""
    import io
    import tempfile

    import pandas as pd
    from docx import Document
    from PIL import Image

    from app import diagrams
    from app.parts import export, generators, spec
    from app.session import Session
    from app.stats import evaluation
    from app.uploads.processing import process_upload

    folder = Path(tempfile.mkdtemp())
    session = Session.create(folder / "selftest.rrg")
    session.save_inputs("ch1.scope", {"scope": "A packaged build check.", "limits": ""})
    session.save_draft("ch1.scope", generators.generate(session, "ch1.scope"), "assembled")
    picture = io.BytesIO()
    Image.new("RGB", (640, 480), (40, 90, 140)).save(picture, format="PNG")
    item = process_upload(picture.getvalue(), "diagram.png")[0]
    session.add_attachment("ch1.framework", "file", item.filename, item.mime, item.data)
    built = export.export_report(session, final=False)
    (folder / "out.docx").write_bytes(built.data)
    document = Document(folder / "out.docx")
    workbook = io.BytesIO()
    pd.DataFrame({"Group": ["IT Expert", "IT Expert", "End-User", "End-User"], "Usability": [5, 4, 3, 4]}).to_excel(workbook, index=False)
    frame = evaluation.read_table(workbook.getvalue(), "r.xlsx")
    cleaned = evaluation.clean(frame, {"Group": "__group__", "Usability": "Usability"})
    actors = {"nodes": [{"id": "a", "label": "Teacher", "type": "Actor"}, {"id": "b", "label": "Borrow items", "type": "Use case"}], "edges": [{"from": "a", "to": "b"}]}
    phases = {"nodes": [{"id": f"p{i}", "label": f"Phase {i}"} for i in range(4)], "options": {"cycle": True}}
    from app import computer_check

    ai_report: dict = {
        "computer_check": [(r["name"], r["status"]) for r in computer_check.run()["results"]],
        "ai_parts": len(spec.ai_spec()["parts"]),
        "diagram_bytes": [len(diagrams.draw("usecase", actors, "Selftest").png), len(diagrams.draw("model", phases).png)],
    }
    if ai.client.configured:  # set RRG_RELAY_URL to include a real request through the relay
        session.save_inputs("setup.system", {"system_name": "Selftest"})
        session.save_inputs("ch1.scope", {"scope": "A packaged build check of the AI path.", "limits": "It covers only this one request today."})
        ai_report["ai_status"] = ai.status()["state"]
        ai_report["ai_write"] = ai.write(session, "ch1.scope")
        ai_report["ai_source_text"] = bool(session.draft("ch1.scope")["html"])
    session.close()
    return {
        "parts_in_spec": len(spec.all_parts()), "docx_images": len(document.inline_shapes),
        "docx_bytes": len(built.data), "t": evaluation.compare_groups(cleaned)["t"], **ai_report,
    }


def main() -> None:
    runtime_log.start()
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.03)
    try:
        screen = webview.screens[0]
        width, height = window_size(screen.width, screen.height)
    except Exception:  # noqa: BLE001 - a default size is fine if the screen cannot be measured
        width, height = 1280, 720
    window = webview.create_window(
        "Research Report Generator", f"http://127.0.0.1:{port}/enter?t={TOKEN}",
        width=width, height=height, min_size=(800, 500),
    )

    window.events.closing += lambda: log.info("window closing")
    window.events.closed += lambda: log.info("window closed")

    def selftest() -> None:
        """Developer check of a packaged build: confirm the launch page really loaded."""
        import json
        import os

        time.sleep(2.5)
        log.info("selftest running")
        result = {"title": window.evaluate_js("document.title"), "button": window.evaluate_js("document.getElementById('new').innerText")}
        try:
            result.update(_exercise_packaged_features())
        except Exception as exc:  # noqa: BLE001 - reported to the developer running the check
            result["error"] = repr(exc)
        Path(os.environ["RRG_SELFTEST_RESULT"]).write_text(json.dumps(result))
        log.info("selftest finished: %s", "error" not in result)
        window.destroy()

    try:
        if "--selftest" in sys.argv:
            webview.start(selftest, gui="edgechromium")
        else:
            webview.start(gui="edgechromium")
    except Exception:  # noqa: BLE001 - nothing else can tell the student why no window appeared
        log.exception("the window could not start")
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, "The app window could not open. This computer may need Microsoft Edge WebView2, which is free from microsoft.com/edge/webview2.", "Research Report Generator", 0x10)
    shutdown()
    log.info("exit")
    logging.shutdown()
    server.should_exit = True
    # The window's .NET runtime can keep the process alive after closing, which also locks the app files.
    os._exit(0)


if __name__ == "__main__":
    main()
