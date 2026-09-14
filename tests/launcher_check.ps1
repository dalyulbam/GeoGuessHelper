# ASCII only -- Windows PowerShell 5.1 reads .ps1 as the system code page.
#
# Exercises run.ps1's preflight (stale-process guard + step-failure stop) without
# starting the server. Three rules learned the hard way while writing this:
#   * do NOT prepend anything to the script -- param() must be the first statement,
#     so a stub function in front silently breaks parameter binding.
#   * stub `uv` via PATH instead, so the script under test stays byte-identical.
#   * the head must run FROM THE PROJECT DIR -- run.ps1 resolves .venv from
#     $PSScriptRoot, so a copy in a temp dir looks for <temp>\.venv and finds
#     nothing, making a working guard look broken.
$ErrorActionPreference = "Continue"
$proj    = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path.Replace("\", "/")
$src     = Get-Content "$proj/run.ps1" -Raw
$head    = $src.Substring(0, $src.IndexOf("# --- run server"))
$venvDir = "$proj/.venv"
$script:fails = 0

$stubDir = Join-Path $env:TEMP ("ggh_uvstub_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $stubDir -Force | Out-Null
[System.IO.File]::WriteAllText((Join-Path $stubDir "uv.cmd"),
    "@echo off`r`nexit /b %FAKE_UV_EXIT%`r`n", [System.Text.Encoding]::ASCII)

$headFile = Join-Path $proj ".runps1_head_test.ps1"
[System.IO.File]::WriteAllText($headFile, $head, [System.Text.Encoding]::ASCII)

function Check($name, $cond, $extra = "") {
    if ($cond) { Write-Output "  PASS  $name" }
    else { Write-Output "  FAIL  $name $extra"; $script:fails++ }
}

function RunHead($params) {
    $old = $env:PATH
    $env:PATH = "$stubDir;$old"
    $out = & powershell.exe -NoProfile -NonInteractive -File $headFile @params 2>&1 | Out-String
    $code = $LASTEXITCODE
    $env:PATH = $old
    return @{ out = $out; code = $code }
}

try {
    Write-Output "(1) no stale process -- should just pass"
    $env:FAKE_UV_EXIT = "0"
    $r = RunHead @()
    Check "exits 0" ($r.code -eq 0) "(exit $($r.code))"
    Check "no stale warning" (-not ($r.out -match "still hold"))

    Write-Output ""
    Write-Output "(2) stale process present -- must stop and say what to do"
    $proc = Start-Process -FilePath "$venvDir/Scripts/python.exe" `
        -ArgumentList '-c "import time; time.sleep(40)"' -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 2
    $r = RunHead @()
    Check "exits 1 (does not plough on)" ($r.code -eq 1) "(exit $($r.code))"
    Check "names the holding PID" ($r.out -match ("PID\s+" + $proc.Id)) "out=$($r.out)"
    Check "offers the fix (-StopStale)" ($r.out -match "StopStale")

    Write-Output ""
    Write-Output "(3) -NoSync must NOT be blocked -- it is the escape hatch we recommend"
    $r = RunHead @("-NoSync")
    Check "exits 0" ($r.code -eq 0) "(exit $($r.code))"
    Check "no stale warning when not syncing" (-not ($r.out -match "still hold"))

    Write-Output ""
    Write-Output "(4) -StopStale actually clears it"
    $r = RunHead @("-StopStale")
    Check "exits 0" ($r.code -eq 0) "(exit $($r.code))"
    Start-Sleep -Milliseconds 800
    $alive = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
    Check "the stale process is gone" ($null -eq $alive)
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue

    Write-Output ""
    Write-Output "(5) a failed step must not fall through -- cause of the same error 3x"
    $env:FAKE_UV_EXIT = "2"
    $r = RunHead @()
    Check "propagates uv exit code" ($r.code -eq 2) "(exit $($r.code))"
    Check "says which step failed" ($r.out -match "failed \(exit 2\)")
    Check "does not attempt the next step" (-not ($r.out -match "playwright"))
    $env:FAKE_UV_EXIT = "0"
}
finally {
    Remove-Item $stubDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $headFile -Force -ErrorAction SilentlyContinue
}

Write-Output ""
if ($script:fails -eq 0) { Write-Output "PASS - 0 failures" }
else { Write-Output "FAIL - $($script:fails) failures" }
exit $(if ($script:fails -eq 0) { 0 } else { 1 })
