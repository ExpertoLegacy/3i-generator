"""A session is one portable SQLite file holding all of a group's work."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.version import VERSION  # noqa: E402

SCHEMA_VERSION = 1
SUFFIX = ".rrg"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS parts(
    part_key TEXT PRIMARY KEY, inputs_json TEXT, draft_json TEXT, source TEXT,
    reviewed_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS draft_versions(
    id INTEGER PRIMARY KEY AUTOINCREMENT, part_key TEXT, draft_json TEXT, source TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS sources(
    key TEXT PRIMARY KEY, type TEXT, origin TEXT, csl_json TEXT, notes TEXT, topics TEXT, verified INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS attachments(
    id INTEGER PRIMARY KEY AUTOINCREMENT, part_key TEXT, kind TEXT, filename TEXT, mime TEXT, data_blob BLOB,
    caption TEXT, consent_confirmed INTEGER DEFAULT 0, is_ai_generated INTEGER DEFAULT 0, position INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS datasets(kind TEXT PRIMARY KEY, mapping_json TEXT, cleaned_json TEXT);
CREATE TABLE IF NOT EXISTS computed_results(kind TEXT PRIMARY KEY, result_json TEXT, computed_at TEXT);
CREATE TABLE IF NOT EXISTS diagram_specs(part_key TEXT PRIMARY KEY, kind TEXT, spec_json TEXT, rendered_attachment_id INTEGER);
CREATE TABLE IF NOT EXISTS ai_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT, part_key TEXT, kind TEXT, provider TEXT, model TEXT,
    prompt_version TEXT, usage TEXT, outcome TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS ai_cache(
    digest TEXT PRIMARY KEY, text TEXT, event_id INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS notes(
    id INTEGER PRIMARY KEY AUTOINCREMENT, part_key TEXT, author_name_typed TEXT, body TEXT, resolved INTEGER DEFAULT 0, created_at TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def data_dir() -> Path:
    base = Path(os.environ.get("RRG_DATA_DIR") or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ResearchReportGenerator")
    base.mkdir(parents=True, exist_ok=True)
    return base


class SessionError(Exception):
    pass


class SessionDamaged(SessionError):
    """The file is unreadable, so a safety copy may be offered."""


def safety_copies(stem: str) -> list[Path]:
    """Safety copies of a report, newest first."""
    folder = data_dir() / "safety-copies"
    return sorted((p for p in folder.glob(f"{stem}-*{SUFFIX}") if p.name[len(stem) + 1:][:8].isdigit()), reverse=True) if folder.exists() else []


class Session:
    def __init__(self, path: Path, *, create: bool = False):
        self.path = Path(path)
        if not create and not self.path.exists():
            raise SessionError(f"This session file no longer exists: {self.path}")
        self.lock = threading.RLock()
        try:
            self.db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
            # A rollback journal keeps the session as one file between saves.
            self.db.execute("PRAGMA journal_mode=DELETE")
            self.db.execute("PRAGMA synchronous=FULL")
            if not create:
                found = self.db.execute("SELECT name FROM sqlite_master WHERE name='meta'").fetchone()
                if not found:
                    raise SessionError("This file is not a saved report session.")
                if self.db.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
                    raise SessionDamaged("This report file is damaged.")
            self.db.executescript(SCHEMA)
        except sqlite3.OperationalError as exc:
            self.db.close()
            if "locked" in str(exc) or "readonly" in str(exc):
                raise SessionError("This report is open in another window or cannot be changed here. Close it there or copy it to this computer.") from exc
            raise SessionDamaged("This report file is damaged.") from exc
        except sqlite3.DatabaseError as exc:
            self.db.close()
            raise SessionDamaged("This file is not a saved report session or it is damaged.") from exc
        except SessionError:
            self.db.close()
            raise
        if create:
            self.set_meta("schema_version", str(SCHEMA_VERSION))
            self.set_meta("created_at", now())
            self.set_meta("app_version", VERSION)
        elif int(self.get_meta("schema_version") or 0) > SCHEMA_VERSION:
            raise SessionError("This session was made by a newer version of the app. Update the app to open it.")

    @classmethod
    def create(cls, path: Path) -> "Session":
        return cls(path, create=True)

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))

    def _ensure(self, key: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO parts(part_key, updated_at) VALUES(?,?)", (key, now()))

    def inputs(self, key: str) -> dict:
        from app.parts.spec import with_fixed  # here, because the spec module is larger than the store

        row = self.db.execute("SELECT inputs_json FROM parts WHERE part_key=?", (key,)).fetchone()
        return with_fixed(key, json.loads(row[0]) if row and row[0] else {})

    def save_inputs(self, key: str, data: dict) -> None:
        from app.parts.spec import with_fixed

        data = with_fixed(key, data)
        with self.lock:
            self._ensure(key)
            self.db.execute(
                "UPDATE parts SET inputs_json=?, updated_at=? WHERE part_key=?",
                (json.dumps(data, ensure_ascii=False), now(), key),
            )
            self.set_meta("updated_at", now())

    def draft(self, key: str) -> dict | None:
        row = self.db.execute("SELECT draft_json FROM parts WHERE part_key=?", (key,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def save_draft(self, key: str, draft: dict, source: str, *, version: bool = True) -> None:
        with self.lock:
            self._ensure(key)
            previous = self.db.execute("SELECT draft_json, source FROM parts WHERE part_key=?", (key,)).fetchone()
            if version and previous and previous[0]:
                self.db.execute(
                    "INSERT INTO draft_versions(part_key,draft_json,source,created_at) VALUES(?,?,?,?)",
                    (key, previous[0], previous[1], now()),
                )
                self.db.execute(
                    "DELETE FROM draft_versions WHERE part_key=? AND id NOT IN "
                    "(SELECT id FROM draft_versions WHERE part_key=? ORDER BY id DESC LIMIT 10)",
                    (key, key),
                )
            self.db.execute(
                "UPDATE parts SET draft_json=?, source=?, reviewed_at=NULL, updated_at=? WHERE part_key=?",
                (json.dumps(draft, ensure_ascii=False), source, now(), key),
            )
            self.set_meta("updated_at", now())

    def version_count(self, key: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM draft_versions WHERE part_key=?", (key,)).fetchone()[0]

    def restore_previous(self, key: str) -> bool:
        with self.lock:
            row = self.db.execute(
                "SELECT id, draft_json, source FROM draft_versions WHERE part_key=? ORDER BY id DESC LIMIT 1", (key,)
            ).fetchone()
            if not row:
                return False
            self.db.execute(
                "UPDATE parts SET draft_json=?, source=?, reviewed_at=NULL, updated_at=? WHERE part_key=?",
                (row[1], row[2], now(), key),
            )
            self.db.execute("DELETE FROM draft_versions WHERE id=?", (row[0],))
            return True

    def set_reviewed(self, key: str, reviewed: bool) -> None:
        with self.lock:
            self._ensure(key)
            self.db.execute("UPDATE parts SET reviewed_at=? WHERE part_key=?", (now() if reviewed else None, key))

    def add_attachment(self, key: str, kind: str, filename: str, mime: str, data: bytes, caption: str = "", *, ai: bool = False, first: bool = False) -> int:
        with self.lock:
            position = 0 if first else self.db.execute("SELECT COALESCE(MAX(position),0)+1 FROM attachments WHERE part_key=?", (key,)).fetchone()[0]
            cursor = self.db.execute(
                "INSERT INTO attachments(part_key,kind,filename,mime,data_blob,caption,position,is_ai_generated) VALUES(?,?,?,?,?,?,?,?)",
                (key, kind, filename, mime, data, caption, position, int(ai)),
            )
            self._ensure(key)
            return cursor.lastrowid

    def attachments(self, key: str, *, options: bool = False) -> list[dict]:
        """Files that belong to the part. With options=True, unchosen AI images instead."""
        test = "=" if options else "<>"
        rows = self.db.execute(
            "SELECT id,part_key,kind,filename,mime,caption,consent_confirmed,is_ai_generated FROM attachments "
            f"WHERE part_key=? AND kind{test}'ai_option' ORDER BY position,id", (key,),
        ).fetchall()
        names = ("id", "part_key", "kind", "filename", "mime", "caption", "consent", "ai")
        return [dict(zip(names, row)) for row in rows]

    def attachment_data(self, attachment_id: int) -> tuple[bytes, str] | None:
        return self.db.execute("SELECT data_blob,mime FROM attachments WHERE id=?", (attachment_id,)).fetchone()

    def update_attachment(self, attachment_id: int, caption: str, consent: bool) -> None:
        with self.lock:
            self.db.execute("UPDATE attachments SET caption=?, consent_confirmed=? WHERE id=?", (caption, int(consent), attachment_id))

    def delete_attachment(self, attachment_id: int) -> None:
        with self.lock:
            self.db.execute("DELETE FROM attachments WHERE id=?", (attachment_id,))

    def attachment_parts(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT DISTINCT part_key FROM attachments WHERE kind<>'ai_option'")}

    def attachment_info(self, attachment_id: int) -> dict | None:
        row = self.db.execute("SELECT part_key,kind,caption,is_ai_generated FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        return dict(zip(("part_key", "kind", "caption", "ai"), row)) if row else None

    def keep_option(self, attachment_id: int) -> None:
        with self.lock:
            self.db.execute("UPDATE attachments SET kind='file', position=(SELECT COALESCE(MAX(position),0)+1 FROM attachments) WHERE id=? AND kind='ai_option'", (attachment_id,))

    def add_note(self, key: str, author: str, body: str) -> int:
        with self.lock:
            return self.db.execute("INSERT INTO notes(part_key,author_name_typed,body,created_at) VALUES(?,?,?,?)", (key, author, body, now())).lastrowid

    def notes(self, key: str) -> list[dict]:
        names = ("id", "part_key", "author", "body", "resolved", "created_at")
        rows = self.db.execute("SELECT id,part_key,author_name_typed,body,resolved,created_at FROM notes WHERE part_key=? ORDER BY resolved,id", (key,)).fetchall()
        return [dict(zip(names, row)) for row in rows]

    def set_note_resolved(self, note_id: int, resolved: bool) -> None:
        with self.lock:
            self.db.execute("UPDATE notes SET resolved=? WHERE id=?", (int(resolved), note_id))

    def open_note_counts(self) -> dict[str, int]:
        return dict(self.db.execute("SELECT part_key, COUNT(*) FROM notes WHERE resolved=0 GROUP BY part_key").fetchall())

    def diagram(self, key: str) -> dict | None:
        row = self.db.execute("SELECT kind,spec_json,rendered_attachment_id FROM diagram_specs WHERE part_key=?", (key,)).fetchone()
        return {"kind": row[0], **json.loads(row[1]), "rendered": row[2]} if row else None

    def save_diagram(self, key: str, kind: str, data: dict, rendered: int | None) -> None:
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO diagram_specs(part_key,kind,spec_json,rendered_attachment_id) VALUES(?,?,?,?)",
                (key, kind, json.dumps(data, ensure_ascii=False), rendered),
            )
            self._ensure(key)
            self.set_meta("updated_at", now())

    def save_dataset(self, kind: str, mapping: dict, cleaned: dict) -> None:
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO datasets(kind,mapping_json,cleaned_json) VALUES(?,?,?)",
                (kind, json.dumps(mapping, ensure_ascii=False), json.dumps(cleaned, ensure_ascii=False)),
            )

    def dataset(self, kind: str) -> dict | None:
        row = self.db.execute("SELECT cleaned_json FROM datasets WHERE kind=?", (kind,)).fetchone()
        return json.loads(row[0]) if row else None

    def log_ai(self, key: str, kind: str, provider: str, model: str, prompt_version: str, usage: dict | None, outcome: str) -> int:
        with self.lock:
            cursor = self.db.execute(
                "INSERT INTO ai_events(part_key,kind,provider,model,prompt_version,usage,outcome,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (key, kind, provider, model, prompt_version, json.dumps(usage or {}), outcome, now()),
            )
            return cursor.lastrowid

    def set_ai_outcome(self, event_id: int, outcome: str) -> None:
        """Record what the student did with a generation. Editing outranks a plain acceptance."""
        with self.lock:
            guard = " AND outcome='generated'" if outcome == "accepted" else ""
            self.db.execute(f"UPDATE ai_events SET outcome=? WHERE id=?{guard}", (outcome, event_id))

    def ai_events(self) -> list[dict]:
        names = ("id", "part_key", "kind", "provider", "model", "prompt_version", "usage", "outcome", "created_at")
        rows = self.db.execute(f"SELECT {','.join(names)} FROM ai_events ORDER BY id").fetchall()
        return [dict(zip(names, row)) for row in rows]

    def ai_cache_get(self, digest: str) -> dict | None:
        row = self.db.execute("SELECT text,event_id FROM ai_cache WHERE digest=?", (digest,)).fetchone()
        return {"text": row[0], "event": row[1]} if row else None

    def ai_cache_put(self, digest: str, text: str, event_id: int) -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO ai_cache(digest,text,event_id,created_at) VALUES(?,?,?,?)", (digest, text, event_id, now()))
            self.db.execute("DELETE FROM ai_cache WHERE digest NOT IN (SELECT digest FROM ai_cache ORDER BY rowid DESC LIMIT 200)")

    def status(self) -> dict[str, str]:
        result = {}
        for key, inputs, draft, reviewed in self.db.execute(
            "SELECT part_key, inputs_json, draft_json, reviewed_at FROM parts"
        ):
            if reviewed:
                result[key] = "reviewed"
            elif draft:
                result[key] = "draft"
            elif inputs and inputs != "{}":
                result[key] = "progress"
        return result

    def copy_to(self, target: Path) -> None:
        """Write a complete copy using SQLite's backup API."""
        with self.lock:
            destination = sqlite3.connect(str(target))
            try:
                self.db.backup(destination)
            finally:
                destination.close()


class AppState:
    """App-level state. Never stored in session files."""

    def __init__(self) -> None:
        self.file = data_dir() / "settings.json"
        try:
            self.data = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def recent(self) -> list[str]:
        return [p for p in self.data.get("recent", []) if Path(p).exists()]

    def remember(self, path: Path) -> None:
        items = [str(path)] + [p for p in self.data.get("recent", []) if p != str(path)]
        self.data["recent"] = items[:8]
        self.save()

    def save(self) -> None:
        self.file.write_text(json.dumps(self.data, indent=1), encoding="utf-8")


def snapshot(session: Session) -> None:
    """Keep the three most recent safety copies of a session."""
    folder = data_dir() / "safety-copies"
    folder.mkdir(exist_ok=True)
    stem = session.path.stem
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    session.copy_to(folder / f"{stem}-{stamp}{SUFFIX}")
    copies = sorted(folder.glob(f"{stem}-*{SUFFIX}"))
    for old in copies[:-3]:
        old.unlink(missing_ok=True)
