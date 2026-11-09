"""Write the Windows file details for the .exe. Unnamed programs look more suspicious to security tools."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / ".tools" / "build" / "version_info.txt"
VERSION = re.search(r'VERSION = "([^"]+)"', (ROOT / "app" / "version.py").read_text(encoding="utf-8")).group(1)
NUMBERS = ", ".join((VERSION.split(".") + ["0", "0", "0"])[:4])

TEXT = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({NUMBERS}), prodvers=({NUMBERS}), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Cabiao Senior High School'),
      StringStruct('FileDescription', 'Research Report Generator'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', 'ResearchReportGenerator'),
      StringStruct('LegalCopyright', 'Cabiao Senior High School'),
      StringStruct('OriginalFilename', 'ResearchReportGenerator.exe'),
      StringStruct('ProductName', 'Research Report Generator'),
      StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(TEXT, encoding="utf-8")
    print(f"Wrote {TARGET}")


if __name__ == "__main__":
    main()
