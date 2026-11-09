"""Run the app in a normal browser for development, with a sample session loaded."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["RRG_NO_TOKEN"] = "1"
os.environ.setdefault("RRG_DATA_DIR", str(Path(tempfile.gettempdir()) / "rrg-dev"))

import uvicorn  # noqa: E402

from app.session import Session  # noqa: E402
from app.web import app, state  # noqa: E402


def main() -> None:
    path = Path(os.environ["RRG_DATA_DIR"])
    path.mkdir(parents=True, exist_ok=True)
    target = path / "Dev Sample.rrg"
    state.session = Session(target) if target.exists() else Session.create(target)
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")


if __name__ == "__main__":
    main()
