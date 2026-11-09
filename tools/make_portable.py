"""Make the no-install ZIP from an up-to-date dist folder, with a fingerprint for checking copies."""
import hashlib
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "dist" / "ResearchReportGenerator"
OUT = ROOT / "dist" / "portable"
TOP = "Research Report Generator"
VERSION = re.search(r'VERSION = "([^"]+)"', (ROOT / "app" / "version.py").read_text(encoding="utf-8")).group(1)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"ResearchReportGenerator-Portable-{VERSION}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.write(ROOT / "packaging" / "HOW TO OPEN.txt", f"{TOP}/HOW TO OPEN.txt")
        for path in sorted(APP.rglob("*")):
            if path.is_file():
                archive.write(path, f"{TOP}/{path.relative_to(APP).as_posix()}")
    digest = hashlib.sha256()
    with open(target, "rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    (OUT / "SHA256.txt").write_text(f"{digest.hexdigest()}  {target.name}\n", encoding="ascii")
    print(f"{target} {target.stat().st_size / 1e6:.0f} MB")
    print(digest.hexdigest())


if __name__ == "__main__":
    main()
