"""Packaging test. Proves every heavy dependency works inside a frozen build."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
import webview
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
RESULT = Path(os.environ.get("SPIKE_RESULT", Path(tempfile.gettempdir()) / "rrg_spike.json"))
app = FastAPI()


@app.get("/", response_class=HTMLResponse)
def index():
    return "<!doctype html><title>spike</title><h1 id=x>local app ok</h1>"


def checks() -> dict:
    out = {"frozen": bool(getattr(sys, "frozen", False)), "python": sys.version.split()[0]}
    tmp = Path(tempfile.mkdtemp())

    import pandas as pd
    from scipy import stats

    out["pandas"] = int(pd.DataFrame({"a": [1, 2]}).a.sum())
    out["ttest"] = round(float(stats.ttest_ind([1, 2, 3, 4], [2, 3, 4, 9], equal_var=False).pvalue), 4)

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.plot([1, 2]);
    plt.savefig(tmp / "c.png")
    out["matplotlib"] = (tmp / "c.png").stat().st_size > 0

    from docx import Document

    doc = Document()
    doc.add_paragraph("x")
    doc.save(tmp / "d.docx")
    out["docx"] = (tmp / "d.docx").exists()

    import nh3, pillow_heif, pypdfium2, openpyxl  # noqa: E401,F401

    out["libs"] = nh3.clean("<b>ok</b><script>x</script>")

    dot = BASE / "graphviz" / "bin" / "dot.exe"
    if not dot.exists():
        dot = BASE / "vendor" / "graphviz" / "bin" / "dot.exe"
    flags = subprocess.CREATE_NO_WINDOW
    done = subprocess.run(
        [str(dot), "-Tpng", "-o", str(tmp / "g.png")],
        input=b"digraph{a->b}", capture_output=True, creationflags=flags, cwd=str(dot.parent), timeout=60,
    )
    out["graphviz_rc"] = done.returncode
    out["graphviz_err"] = done.stderr.decode(errors="replace")[:300]
    out["graphviz_png"] = (tmp / "g.png").exists()
    return out


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    selftest = "--selftest" in sys.argv
    port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None)
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)

    result = {}
    try:
        result = checks()
    except Exception as exc:  # noqa: BLE001 - the spike reports every failure
        result = {"error": repr(exc)}

    window = webview.create_window("Packaging test", f"http://127.0.0.1:{port}/")

    def after_load():
        time.sleep(1.5)
        result["webview_text"] = window.evaluate_js("document.getElementById('x').innerText")
        RESULT.write_text(json.dumps(result, indent=2))
        if selftest:
            window.destroy()

    webview.start(after_load, gui="edgechromium")


if __name__ == "__main__":
    main()
