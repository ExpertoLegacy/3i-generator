# Makes dist\installer\ResearchReportGenerator-Setup-<version>.exe from an up-to-date dist folder.
# Build the app first with packaging\build.ps1. Needs Inno Setup 6.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$candidates = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")
$iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 was not found. Install it from https://jrsoftware.org/isinfo.php" }

$version = (Select-String -Path "app\version.py" -Pattern 'VERSION = "([^"]+)"').Matches[0].Groups[1].Value
& $iscc "/DAppVersion=$version" "/DRootDir=$root" "packaging\installer.iss"
if ($LASTEXITCODE -ne 0) { throw "The installer could not be built." }
Get-Item "dist\installer\ResearchReportGenerator-Setup-$version.exe" | Select-Object FullName, @{N = "MB"; E = { [int]($_.Length / 1MB) } }
