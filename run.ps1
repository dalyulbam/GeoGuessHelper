#Requires -Version 5.1
<#
    GeoGuessHelper launcher

        ./run.ps1            # sync ALL deps (map+capture+analyze), start server, open browser
        ./run.ps1 -Minimal   # base only (link 'extract'); skips Playwright/Pillow/anthropic
        ./run.ps1 -NoSync     # skip uv sync, just run (keeps all extras)
        ./run.ps1 -StopStale  # kill leftover .venv processes that block uv sync, then run

    Requires uv (https://github.com/astral-sh/uv).

    Why "all" is the default: the browser-render capture (headless Chromium) needs the
    Playwright extra, and analysis needs anthropic. Because both `uv sync` and `uv run`
    default to the BASE dependency set, a plain sync/run would UNINSTALL those extras and
    silently break capture + analyze. So we sync AND run with `--extra all`.

    NOTE: this script is intentionally ASCII-only so Windows PowerShell 5.1
    (which reads .ps1 as the system code page, not UTF-8) parses it correctly.
#>
[CmdletBinding()]
param(
    [switch]$Minimal,
    [switch]$NoSync,
    [switch]$StopStale
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# Work behind a TLS-intercepting proxy/AV (otherwise uv fails cert verification).
$env:UV_NATIVE_TLS = "1"
# Console unicode safety (the Python banner / web UI use Korean text).
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

# --- check uv ---
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Host "[!] uv is required. Install:" -ForegroundColor Yellow
    Write-Host "      winget install astral-sh.uv" -ForegroundColor Cyan
    Write-Host "    or  https://github.com/astral-sh/uv"
    exit 1
}

# --- .env hint (server still runs without it; link 'extract' works keyless) ---
if (-not (Test-Path ".env")) {
    Write-Host "[i] No .env found. To enable map/capture/analyze, copy .env.example and add keys:" -ForegroundColor DarkYellow
    Write-Host "      Copy-Item .env.example .env" -ForegroundColor Cyan
    Write-Host "    (link 'extract' works even without keys)"
}

# --- stop-the-world on a failed step ---------------------------------------
# $ErrorActionPreference = "Stop" does NOT catch a native exe returning non-zero.
# Without this, a failed `uv sync` just fell through to the next step and printed
# the SAME error three times, never saying what to do about it.
function Invoke-Step {
    param([string]$What, [scriptblock]$Body)
    Write-Host "[*] $What ..." -ForegroundColor Green
    & $Body
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] failed (exit $LASTEXITCODE): $What" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

# --- leftover processes holding the venv -----------------------------------
# A previous server that did not exit keeps a handle on .venv\Scripts\*.exe, and
# uv sync then dies with "failed to remove file ... (os error 32)". The message
# names the file but not the culprit, so it reads as a broken install.
# Seen for real: a `geoguesshelper serve` from seven days earlier, CPU 0s / RAM 0MB,
# not even listening on the port, still holding the handle (260914).
#
# Only when we are about to sync. With -NoSync nothing gets replaced, so a live
# process is not in the way -- and blocking there would contradict the very
# escape hatch this message recommends.
$venvDir = Join-Path $PSScriptRoot ".venv"
$stale = @()
if ((-not $NoSync) -or $StopStale) {
    $stale = @(Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($venvDir, [StringComparison]::OrdinalIgnoreCase) })
}
if ($stale.Count -gt 0) {
    Write-Host "[!] $($stale.Count) process(es) still hold this project's .venv:" -ForegroundColor Yellow
    foreach ($p in $stale) {
        Write-Host ("      PID {0,-7} {1,-22} started {2}" -f $p.Id, $p.ProcessName, $p.StartTime)
    }
    if ($StopStale) {
        Write-Host "[*] -StopStale given: stopping them ..." -ForegroundColor Green
        foreach ($p in $stale) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Milliseconds 800
    }
    else {
        Write-Host "    uv sync cannot replace files they hold. Close that server, or re-run:" -ForegroundColor Yellow
        Write-Host "      ./run.ps1 -StopStale" -ForegroundColor Cyan
        Write-Host "    (-NoSync also works if you just want to start without syncing.)"
        exit 1
    }
}

# --- sync dependencies ---
if (-not $NoSync) {
    if ($Minimal) {
        Invoke-Step "uv sync (base only)" { uv sync }
    }
    else {
        Invoke-Step "uv sync --extra all" { uv sync --extra all }
        Invoke-Step "installing playwright chromium (no-op if present)" {
            uv run --extra all playwright install chromium
        }
    }
}

# --- run server (auto-opens the browser) ---
# Pass --extra all so `uv run` does NOT strip the capture/analyze extras from the env.
Write-Host "[*] starting server - the browser will open shortly ..." -ForegroundColor Green
if ($Minimal) {
    uv run geoguesshelper-server
}
else {
    uv run --extra all geoguesshelper-server
}
