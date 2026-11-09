# Deploys the AI relay to the project owner's free Cloudflare account. Safe to run again.
# Sign in first:
#   wrangler login
# From the project folder:
#   powershell -ExecutionPolicy Bypass -File relay\deploy.ps1
# Add -UseBrowserLogin when old Cloudflare token variables on this computer override that sign-in.
# They are ignored for this run only and are not changed.
param([switch]$UseBrowserLogin)

$ErrorActionPreference = "Continue"  # wrangler prints warnings to stderr, so exit codes decide failure
$relay = $PSScriptRoot
$root = Split-Path -Parent $relay
Set-Location $relay

function Fail($message) { Write-Output "FAILED: $message"; exit 1 }
function Run($arguments) {
  $script:output = (& cmd /c "wrangler $arguments 2>&1" | Out-String)
  $script:code = $LASTEXITCODE
}

if ($UseBrowserLogin) {
  foreach ($name in "CLOUDFLARE_API_TOKEN", "CF_API_TOKEN", "CLOUDFLARE_API_KEY", "CF_API_KEY", "CF_ACCOUNT_ID", "CLOUDFLARE_ACCOUNT_ID") {
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
  }
}

& "$root\.venv\Scripts\python.exe" "$root\tools\build_relay_prompts.py"
if ($LASTEXITCODE -ne 0) { Fail "Could not build the prompts." }

$tomlPath = Join-Path $relay "wrangler.toml"
$toml = [IO.File]::ReadAllText($tomlPath)
if ($toml.Contains("SET_BY_DEPLOY_SCRIPT")) {
  Run "d1 create rrg_relay"
  $match = [regex]::Match($output, '"?database_id"?\s*[=:]\s*"([0-9a-f-]{36})"')
  if ($code -ne 0 -or -not $match.Success) {
    Run "d1 info rrg_relay --json"  # the database may exist from an earlier attempt
    $match = [regex]::Match($output, '"uuid"\s*:\s*"([0-9a-f-]{36})"')
  }
  if (-not $match.Success) { Fail "Could not create the database.`n$output" }
  [IO.File]::WriteAllText($tomlPath, $toml.Replace("SET_BY_DEPLOY_SCRIPT", $match.Groups[1].Value), (New-Object Text.UTF8Encoding $false))
  Write-Output "Database ready."
}

Run "d1 migrations apply rrg_relay --remote"
if ($code -ne 0) { Fail "Could not prepare the database.`n$output" }
Write-Output "Database tables ready."

# The Worker must exist before a secret can be attached to it.
Run "deploy"
if ($code -ne 0) { Fail "Could not deploy the relay.`n$output" }
$address = [regex]::Match($output, 'https://[\w.-]+\.workers\.dev')
if (-not $address.Success) { Fail "Deploy finished without a relay address.`n$output" }

Run "secret list"
if ($output -notmatch "TOKEN_SECRET") {
  $bytes = New-Object byte[] 32
  [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $secretFile = [IO.Path]::GetTempFileName()
  try {
    [IO.File]::WriteAllText($secretFile, [Convert]::ToBase64String($bytes), (New-Object Text.ASCIIEncoding))
    $script:output = (& cmd /c "wrangler secret put TOKEN_SECRET < `"$secretFile`" 2>&1" | Out-String)
    if ($LASTEXITCODE -ne 0) { Fail "Could not store the signing secret.`n$output" }
  } finally {
    Remove-Item $secretFile -Force
  }
  Write-Output "Signing secret stored."
}

[IO.File]::WriteAllText("$root\spec\relay.json", "{`"url`": `"$($address.Value)`"}`n", (New-Object Text.ASCIIEncoding))
Write-Output "Relay address saved to spec\relay.json: $($address.Value)"
Write-Output "Rebuild the app so it includes this address: powershell -ExecutionPolicy Bypass -File packaging\build.ps1"
