# Runs inside Windows Sandbox: installs the app on clean Windows, runs its self-test, reports back.
$ErrorActionPreference = "Continue"
$desktop = "C:\Users\WDAGUtilityAccount\Desktop"
$results = "$desktop\Results"
$lines = New-Object System.Collections.ArrayList
function Note($text) { [void]$lines.Add($text); $lines | Set-Content "$results\progress.txt" -Encoding utf8 }

Note "Windows build: $([Environment]::OSVersion.Version)"
Note "Python on PATH: $([bool](Get-Command python -ErrorAction SilentlyContinue))"
Note "Word installed: $(Test-Path 'Registry::HKEY_CLASSES_ROOT\Word.Application')"
Note "Bookman fonts: $((Get-ChildItem C:\Windows\Fonts -Filter 'BOOK*.TT*' -ErrorAction SilentlyContinue | Measure-Object).Count)"

$setup = Get-ChildItem "$desktop\Installer\ResearchReportGenerator-Setup-*.exe" | Select-Object -First 1
$started = Get-Date
$install = Start-Process $setup.FullName -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -PassThru
$null = $install.WaitForExit(600000)
Note "Install exit code: $($install.ExitCode) in $([int]((Get-Date) - $started).TotalSeconds) seconds"

$app = "$env:LOCALAPPDATA\Programs\Research Report Generator\ResearchReportGenerator.exe"
Note "App installed: $(Test-Path $app)"
$env:RRG_SELFTEST_RESULT = "$results\selftest.json"
$started = Get-Date
$run = Start-Process $app -ArgumentList "--selftest" -PassThru
$finished = $run.WaitForExit(300000)
Note "Self-test finished by itself: $finished in $([int]((Get-Date) - $started).TotalSeconds) seconds, exit code $($run.ExitCode)"
Note "Self-test result written: $(Test-Path "$results\selftest.json")"

$logs = "$env:LOCALAPPDATA\ResearchReportGenerator"
foreach ($name in "app.log", "crash.log") { if (Test-Path "$logs\$name") { Copy-Item "$logs\$name" "$results\$name" } }
Note "done"
"done" | Set-Content "$results\done.txt"
