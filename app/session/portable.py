"""Work on a local copy when a session lives on a USB drive or network folder.

SQLite can corrupt files on drives that disappear mid-write. So the app edits a copy on the
computer and writes finished copies back to the drive. A write-back goes to a temporary file
first, so pulling the drive out can never damage the file already there.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from .store import Session, SessionError, data_dir

NO_ROOT, REMOVABLE, REMOTE = 1, 2, 4
SAVED, MISSING, FAILED = "saved", "missing", "failed"


def needs_local_copy(path: Path) -> bool:
    text = str(path)
    if text.startswith("\\\\"):
        return True
    drive = Path(text).drive
    if not drive:
        return False
    return ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\") in (NO_ROOT, REMOVABLE, REMOTE)


def _stat(path: Path) -> list | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return [info.st_mtime_ns, info.st_size]


class PortableCopy:
    def __init__(self, origin: Path):
        self.origin = Path(origin)
        key = hashlib.sha256(str(self.origin).lower().encode("utf-8")).hexdigest()[:12]
        self.folder = data_dir() / "working" / key
        self.working = self.folder / self.origin.name
        self.marker = self.folder / "synced.json"
        self.changes_seen: int | None = None

    def _read_marker(self) -> dict | None:
        try:
            return json.loads(self.marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write_marker(self) -> None:
        self.marker.write_text(json.dumps({"working": _stat(self.working), "origin": _stat(self.origin)}), encoding="utf-8")

    def _copy_from_origin(self) -> None:
        part = self.working.with_name(self.working.name + ".part")
        try:
            shutil.copy2(self.origin, part)
        except OSError as exc:
            raise SessionError("This report could not be read from the drive. Check the drive and try again.") from exc
        os.replace(part, self.working)
        self._write_marker()

    def prepare(self, create: bool = False) -> str:
        """Set up the local copy and return a message when the student should know something."""
        self.folder.mkdir(parents=True, exist_ok=True)
        if create:
            self.working.unlink(missing_ok=True)
            self.marker.unlink(missing_ok=True)
            return ""
        marker = self._read_marker()
        have_work = self.working.exists()
        origin_now = _stat(self.origin)
        if origin_now is None:
            if not have_work:
                raise SessionError("This report is on a drive that is not connected. Connect it and try again.")
            return "The drive with this report is not connected. You are working on the copy saved on this computer."
        if not have_work or marker is None:
            self._copy_from_origin()
            return ""
        unsaved = _stat(self.working) != marker["working"]
        changed_elsewhere = origin_now != marker["origin"]
        if unsaved and changed_elsewhere:
            kept = data_dir() / "recovered" / f"{self.origin.stem} unsaved {datetime.now():%Y%m%d-%H%M%S}{self.origin.suffix}"
            kept.parent.mkdir(exist_ok=True)
            shutil.copy2(self.working, kept)
            self._copy_from_origin()
            return f"This report changed on another computer, so the drive's version was opened. Your earlier unsaved work was kept as {kept.name} in the app's recovered folder."
        if unsaved:
            return "Work that had not reached the drive last time was recovered."
        if changed_elsewhere:
            self._copy_from_origin()
        return ""

    def write_back(self, session: Session) -> str:
        """Copy the session to the drive if it changed. The drive's file is replaced only by a verified copy."""
        changes = session.db.total_changes
        if changes == self.changes_seen and _stat(self.origin) is not None:
            return SAVED
        if not self.origin.parent.exists():
            return MISSING
        temporary = self.origin.with_name(self.origin.name + ".saving")
        try:
            session.copy_to(temporary)
            with open(temporary, "rb+") as handle:
                os.fsync(handle.fileno())
            check = sqlite3.connect(str(temporary))
            try:
                if check.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
                    return FAILED
            finally:
                check.close()
            os.replace(temporary, self.origin)
        except (OSError, sqlite3.Error):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return MISSING if not self.origin.parent.exists() else FAILED
        self.changes_seen = changes
        self._write_marker()
        return SAVED
