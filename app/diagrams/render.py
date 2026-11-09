"""Turn a table of boxes and connections into a print-ready PNG.

All text is escaped into Graphviz HTML labels, and every other DOT attribute is fixed here,
so nothing a student or the AI types can reach a file path or a Graphviz option.
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))
FROZEN_DOT = BASE / "graphviz" / "bin" / "dot.exe"
DOT = FROZEN_DOT if FROZEN_DOT.exists() else BASE / "vendor" / "graphviz" / "bin" / "dot.exe"

DPI = 220
FONT_POINTS = 12
PAGE_WIDTH_INCHES = 6.0  # the template's text width, less a small margin
PAGE_HEIGHT_INCHES = 8.5
SMALLEST_READABLE_POINTS = 8.0
MAX_NODES = 40
MAX_EDGES = 60
MAX_LABEL = 200
MAX_DETAILS = 1500
RESERVED_IDS = {"node", "edge", "graph", "digraph", "subgraph", "strict", "input", "process", "output"}  # DOT keywords and the IPO boxes
TWO_DIRECTIONS = {"architecture", "dfd", "erd"}  # long chains print larger when drawn top to bottom

KINDS: dict[str, dict] = {
    "ipo": {
        "name": "Input-Process-Output", "item": "Item", "types": ["Input", "Process", "Output"],
        "options": {"feedback": "Show a feedback arrow from Output back to Input"},
    },
    "model": {
        "name": "Development model", "item": "Phase", "ordered": True,
        "options": {"cycle": "The phases repeat in a cycle"},
    },
    "architecture": {
        "name": "System architecture", "item": "Component", "types": ["User", "Interface", "Server", "Database", "External service"],
        "edges": True, "edge_label": "What passes between them",
    },
    "usecase": {
        "name": "Use case diagram", "item": "Actor or use case", "types": ["Actor", "Use case"],
        "edges": True, "edge_note": "Connect each actor to what it does.",
    },
    "dfd": {
        "name": "Data flow diagram", "item": "Element", "types": ["External entity", "Process", "Data store"],
        "edges": True, "edge_label": "Data that flows",
    },
    "erd": {
        "name": "Entity relationship diagram", "item": "Table", "details": "Fields, one per line. Mark keys like: student_id PK",
        "edges": True, "edge_label": "Relationship", "edge_choices": ["1:1", "1:N", "N:M"],
    },
}


class DiagramError(Exception):
    """A problem described in words a student can act on."""


@dataclass
class RenderResult:
    png: bytes
    printed_font_points: float
    warnings: list[str] = field(default_factory=list)


# ---- cleaning -------------------------------------------------------------------------


def _text(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def clean_spec(kind: str, raw: dict | None) -> dict:
    """Keep only known fields. Connections to missing boxes are dropped."""
    if kind not in KINDS:
        raise DiagramError("This kind of diagram is not available here.")
    config = KINDS[kind]
    raw = raw or {}
    types = config.get("types")
    nodes: list[dict] = []
    seen: set[str] = set()
    safe_ids: dict[str, str] = {}  # IDs go into Graphviz source unquoted, so only plain ones survive
    for index, item in enumerate((raw.get("nodes") or [])[:MAX_NODES], start=1):
        if not isinstance(item, dict):
            continue
        label = _text(item.get("label"), MAX_LABEL)
        if not label:
            continue
        given = _text(item.get("id"), 40)
        node_id = given if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", given) and given.lower() not in RESERVED_IDS else f"n{index}"
        while node_id in seen:
            node_id += "x"
        seen.add(node_id)
        safe_ids.setdefault(given, node_id)
        node_type = item.get("type") if types and item.get("type") in types else (types[0] if types else "")
        nodes.append({"id": node_id, "label": label, "type": node_type, "details": _text(item.get("details"), MAX_DETAILS) if config.get("details") else ""})
    edges: list[dict] = []
    if config.get("edges"):
        for item in (raw.get("edges") or [])[:MAX_EDGES]:
            if not isinstance(item, dict):
                continue
            start, end = safe_ids.get(_text(item.get("from"), 40)), safe_ids.get(_text(item.get("to"), 40))
            if start and end and start != end:
                label = _text(item.get("label"), 80)
                choices = config.get("edge_choices")
                if choices and label not in choices:
                    label = choices[1]
                edges.append({"from": start, "to": end, "label": label})
    options = {name: bool((raw.get("options") or {}).get(name)) for name in config.get("options", {})}
    return {"nodes": nodes, "edges": edges, "options": options}


def _lines(text: str | None) -> list[str]:
    return [line.strip(" -•\t") for line in (text or "").splitlines() if line.strip(" -•\t")]


def starter(kind: str, values: dict, system: dict) -> dict:
    """A first version built only from the student's own answers, so no AI is needed."""
    nodes: list[dict] = []

    def add(label: str, node_type: str = "") -> str:
        nodes.append({"id": f"n{len(nodes) + 1}", "label": label, "type": node_type, "details": ""})
        return nodes[-1]["id"]

    edges: list[dict] = []
    options: dict = {}
    if kind == "ipo":
        for field_id, node_type in (("input", "Input"), ("process", "Process"), ("output", "Output")):
            for line in _lines(values.get(field_id)):
                add(line, node_type)
    elif kind == "model":
        name = (system.get("dev_model") or "").lower()
        for phase in ("Requirements Planning and Analysis", "System Design", "Development (Coding)", "Testing", "Deployment and Evaluation"):
            add(phase)
        options["cycle"] = any(word in name for word in ("agile", "scrum", "iterative", "prototyp", "rapid"))
    elif kind == "architecture":
        user = add("Users", "User")
        interface = add(f"{system.get('platform') or 'Web'} interface".capitalize(), "Interface")
        server = add("Application server", "Server")
        database = add("Database", "Database")
        edges = [{"from": user, "to": interface, "label": ""}, {"from": interface, "to": server, "label": ""}, {"from": server, "to": database, "label": ""}]
    elif kind == "usecase":
        actors = [add(role, "Actor") for role in _lines(system.get("roles"))]
        cases = [add(module, "Use case") for module in _lines(system.get("modules"))]
        edges = [{"from": a, "to": c, "label": ""} for a in actors[:1] for c in cases]
    return {"nodes": nodes, "edges": edges, "options": options}


# ---- DOT ------------------------------------------------------------------------------


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _wrapped(text: str, width: int) -> str:
    return "<BR/>".join(_esc(line) for line in textwrap.wrap(text, width, break_long_words=True) or [""])


def _left(text: str, width: int) -> str:
    return '<BR ALIGN="LEFT"/>'.join(_esc(line) for line in textwrap.wrap(text, width, break_long_words=True) or [""]) + '<BR ALIGN="LEFT"/>'


def _header(rankdir: str = "LR", extra: str = "") -> str:
    return (
        f'digraph G {{ charset="UTF-8"; rankdir={rankdir}; bgcolor="white"; pad=0.1; nodesep=0.4; ranksep=0.55; {extra}\n'
        f'node [fontname="Arial", fontsize={FONT_POINTS}, color="black", penwidth=1.1, margin="0.12,0.08"];\n'
        f'edge [fontname="Arial", fontsize={FONT_POINTS - 1}, color="black", penwidth=1.1, arrowsize=0.8];\n'
    )


def _ipo(spec: dict) -> str:
    dot = _header()
    groups = [("Input", "INPUT"), ("Process", "PROCESS"), ("Output", "OUTPUT")]
    for name, title in groups:
        rows = "".join(f'• {_left(n["label"], 19)}' for n in spec["nodes"] if n["type"] == name) or " "
        dot += (
            f'{name} [shape=plain, label=<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="7">'
            f'<TR><TD BGCOLOR="#E4E4E4"><B>{title}</B></TD></TR><TR><TD ALIGN="LEFT" BALIGN="LEFT" WIDTH="130">{rows}</TD></TR></TABLE>>];\n'
        )
    dot += "Input -> Process; Process -> Output;\n"
    if spec["options"].get("feedback"):
        dot += 'Output:s -> Input:s [style=dashed, constraint=false, label=<Feedback>];\n'
    return dot + "}"


def _model(spec: dict) -> tuple[str, str]:
    cycle = spec["options"].get("cycle")
    dot = _header("TB", 'mindist=0.7; ' if cycle else "")
    nodes = spec["nodes"]
    for number, node in enumerate(nodes, start=1):
        dot += f'{node["id"]} [shape=box, style=rounded, label=<{_wrapped(f"{number}. {node['label']}", 20)}>];\n'
    for first, second in zip(nodes, nodes[1:]):
        dot += f'{first["id"]} -> {second["id"]};\n'
    if cycle and len(nodes) > 2:
        dot += f'{nodes[-1]["id"]} -> {nodes[0]["id"]};\n'
    return dot + "}", "circo" if cycle and len(nodes) > 2 else "dot"


SHAPES = {
    "User": 'shape=box, style=rounded', "Interface": 'shape=box', "Server": 'shape=box3d',
    "Database": 'shape=cylinder, margin="0.14,0.14"', "External service": 'shape=component',
}


def _edge_label(text: str) -> str:
    return f", label=<{_wrapped(text, 16)}>" if text else ""


def _architecture(spec: dict, rankdir: str) -> str:
    dot = _header(rankdir)
    for node in spec["nodes"]:
        dot += f'{node["id"]} [{SHAPES[node["type"]]}, label=<{_wrapped(node["label"], 18)}>];\n'
    for edge in spec["edges"]:
        dot += f'{edge["from"]} -> {edge["to"]} [dir=both{_edge_label(edge["label"])}];\n'
    return dot + "}"


def _actor_png(folder: Path) -> str:
    size = 4
    image = Image.new("RGB", (40 * size, 72 * size), "white")
    pen = ImageDraw.Draw(image)
    width = 2 * size
    pen.ellipse((12 * size, 2 * size, 28 * size, 18 * size), outline="black", width=width)
    pen.line((20 * size, 18 * size, 20 * size, 46 * size), fill="black", width=width)
    pen.line((4 * size, 28 * size, 36 * size, 28 * size), fill="black", width=width)
    pen.line((20 * size, 46 * size, 6 * size, 70 * size), fill="black", width=width)
    pen.line((20 * size, 46 * size, 34 * size, 70 * size), fill="black", width=width)
    path = folder / "actor.png"
    image.save(path)
    return path.name


def _usecase(spec: dict, folder: Path, system_name: str) -> str:
    picture = _actor_png(folder)
    dot = _header()
    actors = [n for n in spec["nodes"] if n["type"] == "Actor"]
    cases = [n for n in spec["nodes"] if n["type"] == "Use case"]
    for node in actors:
        dot += (
            f'{node["id"]} [shape=plain, label=<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1">'
            f'<TR><TD FIXEDSIZE="TRUE" WIDTH="30" HEIGHT="54"><IMG SRC="{picture}" SCALE="TRUE"/></TD></TR>'
            f'<TR><TD>{_wrapped(node["label"], 14)}</TD></TR></TABLE>>];\n'
        )
    dot += f'subgraph cluster_system {{ label=<<B>{_wrapped(system_name, 40)}</B>>; fontname="Arial"; fontsize={FONT_POINTS}; color="black"; penwidth=1.1; margin=16;\n'
    for node in cases:
        dot += f'{node["id"]} [shape=ellipse, label=<{_wrapped(node["label"], 18)}>];\n'
    dot += "}\n"
    for edge in spec["edges"]:
        dot += f'{edge["from"]} -> {edge["to"]} [dir=none];\n'
    return dot + "}"


def _dfd(spec: dict, rankdir: str) -> str:
    dot = _header(rankdir)
    process, store = 0, 0
    for node in spec["nodes"]:
        if node["type"] == "Process":
            process += 1
            dot += (
                f'{node["id"]} [shape=plain, label=<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" STYLE="ROUNDED" CELLPADDING="5">'
                f'<TR><TD SIDES="B" BORDER="1">{process}.0</TD></TR><TR><TD>{_wrapped(node["label"], 16)}</TD></TR></TABLE>>];\n'
            )
        elif node["type"] == "Data store":
            store += 1
            dot += (
                f'{node["id"]} [shape=plain, label=<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="6">'
                f'<TR><TD SIDES="TBL">D{store}</TD><TD SIDES="TB">{_wrapped(node["label"], 18)}</TD></TR></TABLE>>];\n'
            )
        else:
            dot += f'{node["id"]} [shape=box, penwidth=1.8, label=<{_wrapped(node["label"], 16)}>];\n'
    for edge in spec["edges"]:
        dot += f'{edge["from"]} -> {edge["to"]} [{_edge_label(edge["label"]).lstrip(", ")}];\n'
    return dot + "}"


def _erd(spec: dict, rankdir: str) -> str:
    dot = _header(rankdir)
    for node in spec["nodes"]:
        rows = ""
        for line in _lines(node["details"]):
            words = line.split()
            key = words[-1].upper() if len(words) > 1 and words[-1].upper() in ("PK", "FK") else ""
            name = " ".join(words[:-1]) if key else line
            rows += f'<TR><TD ALIGN="LEFT">{_wrapped(name, 22)}</TD><TD ALIGN="RIGHT">{key or " "}</TD></TR>'
        rows = rows or '<TR><TD COLSPAN="2"> </TD></TR>'
        dot += (
            f'{node["id"]} [shape=plain, label=<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="4">'
            f'<TR><TD COLSPAN="2" BGCOLOR="#E4E4E4"><B>{_wrapped(node["label"], 24)}</B></TD></TR>{rows}</TABLE>>];\n'
        )
    ends = {"1:1": ("tee", "tee"), "1:N": ("tee", "crow"), "N:M": ("crow", "crow")}
    for edge in spec["edges"]:
        tail, head = ends[edge["label"]]
        dot += f'{edge["from"]} -> {edge["to"]} [dir=both, arrowtail={tail}, arrowhead={head}, arrowsize=1.1];\n'
    return dot + "}"


def build_dot(kind: str, spec: dict, folder: Path, system_name: str, rankdir: str = "LR") -> tuple[str, str]:
    """Return DOT text and the Graphviz layout engine."""
    if kind == "ipo":
        return _ipo(spec), "dot"
    if kind == "model":
        return _model(spec)
    if kind == "architecture":
        return _architecture(spec, rankdir), "dot"
    if kind == "usecase":
        return _usecase(spec, folder, system_name), "dot"
    if kind == "dfd":
        return _dfd(spec, rankdir), "dot"
    return _erd(spec, rankdir), "dot"


def _requirements(kind: str, spec: dict) -> None:
    if not spec["nodes"]:
        raise DiagramError(f"Add at least one {KINDS[kind]['item'].lower()} first.")
    types = {n["type"] for n in spec["nodes"]}
    if kind == "ipo" and not {"Input", "Process", "Output"} <= types:
        raise DiagramError("Add at least one item each for Input, Process, and Output.")
    if kind == "usecase" and not {"Actor", "Use case"} <= types:
        raise DiagramError("Add at least one actor and one use case.")
    if kind in ("usecase", "architecture", "dfd", "erd") and len(spec["nodes"]) > 1 and not spec["edges"]:
        raise DiagramError("Add the connections between your boxes.")


def _run_graphviz(source: str, engine: str, folder: Path) -> bytes:
    environment = {**os.environ, "PATH": f"{DOT.parent}{os.pathsep}{os.environ.get('PATH', '')}", "GVBINDIR": str(DOT.parent)}
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        done = subprocess.run(
            [str(DOT), f"-K{engine}", "-Tpng", f"-Gdpi={DPI}"], input=source.encode("utf-8"), capture_output=True,
            cwd=folder, env=environment, timeout=30, creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DiagramError("The diagram could not be drawn. Make it simpler and try again.") from exc
    if done.returncode != 0 or not done.stdout.startswith(b"\x89PNG"):
        raise DiagramError("The diagram could not be drawn. Check your entries and try again.")
    return done.stdout


def _printed_points(png: bytes) -> float:
    with Image.open(io.BytesIO(png)) as image:
        width, height = image.size
    return round(FONT_POINTS * min(1.0, PAGE_WIDTH_INCHES / (width / DPI), PAGE_HEIGHT_INCHES / (height / DPI)), 1)


def draw(kind: str, raw: dict, system_name: str = "System") -> RenderResult:
    spec = clean_spec(kind, raw)
    _requirements(kind, spec)
    directions = ("LR", "TB") if kind in TWO_DIRECTIONS else ("LR",)
    best: tuple[float, bytes] | None = None
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        for direction in directions:
            source, engine = build_dot(kind, spec, folder, system_name or "System", direction)
            png = _run_graphviz(source, engine, folder)
            printed = _printed_points(png)
            if best is None or printed > best[0]:
                best = (printed, png)
            if printed >= FONT_POINTS:  # already full size, so this direction stands
                break
    printed, png = best
    warnings = []
    if printed < SMALLEST_READABLE_POINTS:
        warnings.append(f"This diagram has so much in it that its text will print at about {printed} points. Use shorter names, or split it into two diagrams.")
    return RenderResult(png, printed, warnings)
