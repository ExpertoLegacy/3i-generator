# Tests the installer on a disposable clean Windows. Needs the Windows Sandbox feature.
#   powershell -ExecutionPolicy Bypass -File packaging\sandbox\run_clean_test.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$sandbox = "$env:SystemRoot\System32\WindowsSandbox.exe"
if (-not (Test-Path $sandbox)) { throw "Windows Sandbox is not enabled on this computer." }
$results = "$root\dist\sandbox-results"
Remove-Item $results -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $results | Out-Null

$config = "$results\clean-test.wsb"
@"
<Configuration>
  <VGpu>Disable</VGpu>
  <Networking>Enable</Networking>
  <MappedFolders>
    <MappedFolder><HostFolder>$root\dist\installer</HostFolder><SandboxFolder>C:\Users\WDAGUtilityAccount\Desktop\Installer</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$root\packaging\sandbox</HostFolder><SandboxFolder>C:\Users\WDAGUtilityAccount\Desktop\Script</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$results</HostFolder><SandboxFolder>C:\Users\WDAGUtilityAccount\Desktop\Results</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>powershell -ExecutionPolicy Bypass -File C:\Users\WDAGUtilityAccount\Desktop\Script\inside.ps1</Command></LogonCommand>
</Configuration>
"@ | Set-Content $config -Encoding utf8

Start-Process $config
$deadline = (Get-Date).AddMinutes(15)
while (-not (Test-Path "$results\done.txt") -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 5 }
if (Test-Path "$results\progress.txt") { Get-Content "$results\progress.txt" } else { "The sandbox did not report anything." }
if (Test-Path "$results\selftest.json") { Get-Content "$results\selftest.json" }
if (Test-Path "$results\app.log") { Get-Content "$results\app.log" -Tail 8 }
if ((Test-Path "$results\crash.log") -and (Get-Item "$results\crash.log").Length -gt 0) { "CRASH LOG:"; Get-Content "$results\crash.log" -Tail 30 }
