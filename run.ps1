#Requires -Version 5.1
<#
    GeoGuessHelper launcher

        ./run.ps1            # sync ALL deps (map+capture+analyze), start server, open browser
        ./run.ps1 -Minimal   # base only (link 'extract'); skips Playwright/Pillow/anthropic
        ./run.ps1 -NoSync     # skip uv sync, just run (keeps all extras)

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
    [switch]$NoSync
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

# --- sync dependencies ---
if (-not $NoSync) {
    if ($Minimal) {
        Write-Host "[*] uv sync (base only) ..." -ForegroundColor Green
        uv sync
    }
    else {
        Write-Host "[*] uv sync --extra all ..." -ForegroundColor Green
        uv sync --extra all
        Write-Host "[*] installing playwright chromium (no-op if present) ..." -ForegroundColor Green
        uv run --extra all playwright install chromium
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
