"""Build the sample report and one single-part download into a chosen folder."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.docbuild import BuildOptions, build_report, build_single_part  # noqa: E402
from tests.fixtures.complete_report import facts, options_questions, parts  # noqa: E402

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
options = BuildOptions(final=True, research_questions=options_questions())
result = build_report(facts(), parts(), options)
(out / "report.docx").write_bytes(result.data)
(out / "part.docx").write_bytes(build_single_part(facts(), parts(), "ch3.statistics", options))
print("issues:", result.issues)
print("tables:", result.tables)
print("figures:", result.figures)
print("appendices:", result.appendices)
