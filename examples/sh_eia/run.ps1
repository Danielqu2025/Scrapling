# Start Shanghai EIA web app on Windows (loads .env if present).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$envFile = Join-Path $Root ".env"
if (Test-Path $envFile) {
  Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $parts = $line -split "=", 2
    if ($parts.Count -ne 2) { return }
    $name = $parts[0].Trim()
    $value = $parts[1].Trim().Trim("'").Trim('"')
    Set-Item -Path "Env:$name" -Value $value
  }
  Write-Host "Loaded .env"
}

if (-not $env:SH_EIA_HOST) { $env:SH_EIA_HOST = "0.0.0.0" }
if (-not $env:SH_EIA_PORT) { $env:SH_EIA_PORT = "8080" }

$python = $null
foreach ($candidate in @(
  (Join-Path $Root ".venv\Scripts\python.exe"),
  (Join-Path $Root "..\..\.venv\Scripts\python.exe")
)) {
  if (Test-Path $candidate) { $python = $candidate; break }
}
if (-not $python) { $python = "python" }

Write-Host "Starting sh_eia on $($env:SH_EIA_HOST):$($env:SH_EIA_PORT) (auth=$($env:SH_EIA_AUTH_ENABLED))"
& $python (Join-Path $Root "04_run_server.py")
