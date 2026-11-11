"""Read a student's system from a public GitHub link or a local folder, for Chapter 4.

Only a summary of the code leaves the computer. Secret files are never opened, and anything
that looks like a password or key is hidden first.
"""
from __future__ import annotations

import io
import re
import zipfile
from collections import Counter
from pathlib import Path

import httpx

from app import net

MAX_DOWNLOAD = 40 * 1024 * 1024
MAX_FILE = 300_000
MAX_FILES = 6000
MAX_DIGEST = 18_000
SKIP_DIRS = {"node_modules", "vendor", ".git", "dist", "build", "__pycache__", ".venv", "venv", "env", "bin", "obj", ".next", ".idea", ".vscode", "storage", "target", ".gradle", "coverage"}
SECRET = re.compile(r"(^|/)(\.env[^/]*|.*\.(pem|key|p12|pfx|keystore|jks|sqlite3?|db)|id_rsa[^/]*|credentials[^/]*|secrets?[^/]*|.*service[-_]account[^/]*)$", re.IGNORECASE)
HIDE_VALUE = re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|token|private[_-]?key|db_password)\b(['\"]?\s*[:=]>?\s*)(['\"]?)[^\s'\",;]+")
LANGUAGES = {".py": "Python", ".php": "PHP", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".java": "Java", ".kt": "Kotlin", ".dart": "Dart", ".cs": "C#", ".vue": "Vue", ".html": "HTML", ".css": "CSS", ".sql": "SQL", ".go": "Go", ".rb": "Ruby", ".swift": "Swift"}
MANIFESTS = {"package.json", "composer.json", "requirements.txt", "pyproject.toml", "pom.xml", "build.gradle", "pubspec.yaml", "gemfile"}
GITHUB = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")


class RepoError(Exception):
    """A problem described in words a student can act on."""


def _usable(path: str) -> bool:
    return not set(path.lower().split("/")[:-1]) & SKIP_DIRS and not SECRET.search(path)


def redact(text: str) -> str:
    return HIDE_VALUE.sub(r"\1\2\3[hidden]", text)


def _is_schema(path: str) -> bool:
    low = path.lower()
    return low.endswith((".sql", "schema.prisma", "models.py")) or "/migrations/" in low or "/entity/" in low or "/models/" in low


def _is_route(path: str) -> bool:
    low = path.lower()
    return "/routes/" in f"/{low}" or low.endswith(("urls.py", "routes.js", "routes.ts", "router.js", "router.ts", "app.py"))


def digest(name: str, paths: list[str], read) -> dict:
    """Summarize a project. ``read`` returns a file's text, or an empty string if it cannot be read."""
    paths = sorted(p for p in paths if _usable(p))[:MAX_FILES]
    if not paths:
        raise RepoError("No readable code was found here. || Walang mababasang code dito.")
    counts = Counter(LANGUAGES[Path(p).suffix.lower()] for p in paths if Path(p).suffix.lower() in LANGUAGES)
    sections = [f"PROJECT: {name}", "LANGUAGES: " + ", ".join(f"{k} ({v} files)" for k, v in counts.most_common()), "FILES:\n" + "\n".join(paths[:250])]

    def collect(title: str, chosen: list[str], limit: int) -> None:
        used = 0
        lines = []
        for path in chosen:
            text = redact(read(path))
            if not text.strip():
                continue
            piece = text[: min(limit - used, 3000)]
            lines.append(f"--- {path}\n{piece}")
            used += len(piece)
            if used >= limit:
                break
        if lines:
            sections.append(f"{title}:\n" + "\n".join(lines))

    collect("README", [p for p in paths if Path(p).name.lower().startswith("readme") and p.count("/") == 0], 2500)
    collect("DEPENDENCIES", [p for p in paths if Path(p).name.lower() in MANIFESTS], 2000)
    collect("DATABASE", [p for p in paths if _is_schema(p)], 7000)
    collect("ROUTES", [p for p in paths if _is_route(p)], 3000)
    return {"name": name, "files": len(paths), "text": "\n\n".join(sections)[:MAX_DIGEST]}


def from_zip(name: str, raw: bytes) -> dict:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise RepoError("The repository could not be read. || Hindi mabasa ang na-download na repository.") from exc
    entries = {i.filename.split("/", 1)[1]: i for i in archive.infolist() if not i.is_dir() and "/" in i.filename}

    def read(path: str) -> str:
        info = entries[path]
        if info.file_size > MAX_FILE:
            return ""
        return archive.read(info).decode("utf-8", "ignore")

    return digest(name, list(entries), read)


def from_github(link: str, client: httpx.Client | None = None) -> dict:
    match = GITHUB.match(link.strip())
    if not match:
        raise RepoError("Use a link like https://github.com/name/project. || Gumamit ng link na tulad nito.")
    owner, repo = match.groups()
    owned = client is None
    client = client or httpx.Client(transport=net.transport(), timeout=httpx.Timeout(60, connect=10), follow_redirects=True)
    try:
        with client.stream("GET", f"https://codeload.github.com/{owner}/{repo}/zip/HEAD") as response:
            if response.status_code == 404:
                raise RepoError("Not found. It must be public, or choose the folder instead. || Hindi makita ang repository. Dapat ay public ito, o pumili ng folder.")
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data += chunk
                if len(data) > MAX_DOWNLOAD:
                    raise RepoError("Too large. Choose the folder on this computer instead. || Masyadong malaki. Pumili ng folder sa computer.")
    except httpx.HTTPError as exc:
        raise RepoError(f"{net.explain(exc)}") from exc
    finally:
        if owned:
            client.close()
    return from_zip(f"{owner}/{repo}", bytes(data))


def from_folder(folder: Path) -> dict:
    root = Path(folder)
    paths = []
    for path in root.rglob("*"):
        if len(paths) >= MAX_FILES * 3:
            break
        if path.is_file():
            paths.append(path.relative_to(root).as_posix())

    def read(relative: str) -> str:
        path = root / relative
        try:
            return "" if path.stat().st_size > MAX_FILE else path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    return digest(root.name, paths, read)
