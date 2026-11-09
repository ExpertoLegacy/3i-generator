# Builds the standalone app into dist\ResearchReportGenerator. Run from the project folder:
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

& .venv\Scripts\python.exe tools\make_master.py
& .venv\Scripts\python.exe tools\extract_guidelines.py
& .venv\Scripts\python.exe tools\make_icon.py
& .venv\Scripts\python.exe tools\make_version_info.py

& .venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --onedir `
  --name ResearchReportGenerator `
  --icon "$root\packaging\app.ico" `
  --version-file "$root\.tools\build\version_info.txt" --noupx `
  --distpath dist --workpath .tools\build\app-work --specpath .tools\build `
  --paths "$root" `
  --add-data "$root\vendor\graphviz;graphviz" `
  --add-data "$root\template\master.docx;template" `
  --add-data "$root\spec;spec" `
  --add-data "$root\app\templates;app\templates" `
  --add-data "$root\app\static;app\static" `
  --collect-all webview --collect-all clr_loader --collect-all pythonnet `
  --collect-submodules uvicorn --collect-all pillow_heif `
  --collect-all pypdfium2 --collect-all pypdfium2_raw `
  --hidden-import multipart --hidden-import python_multipart `
  app\main.py
if ($LASTEXITCODE -ne 0) { throw "Packaging failed" }
& .venv\Scripts\python.exe tools\make_portable.py
Write-Output "Built: $root\dist\ResearchReportGenerator\ResearchReportGenerator.exe"
