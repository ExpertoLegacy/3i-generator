"""Technical figures drawn by the bundled Graphviz from an editable table. Works fully offline."""
from .render import KINDS, DiagramError, RenderResult, clean_spec, draw, starter

__all__ = ["KINDS", "DiagramError", "RenderResult", "clean_spec", "draw", "starter"]
