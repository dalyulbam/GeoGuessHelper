# snapshot-knowledge.ps1 — 지식의 **스냅샷 세 겹**을 같은 시점으로 고정한다: 분자(molecule) · 관측(observe) 시계열 · 배포 복사본.
#
# 이름이 'snapshot-' 인 이유: 이 스크립트는 원자를 갱신하지 **않는다**. 원자는 잡(P1·P2·P3 적재)·정정 루프·대화가
# 쌓는다. 분자는 "그 시점의 원자 집합에 대한 함수"(molecule/README.md)라 원자가 늘면 임계도 이웃도 바뀐다.
# 그래서 원자가 쌓인 날 밤, 그 시점을 세 겹으로 얼린다 —
#   ① 분자 스냅샷(molecule/snapshots/<ts>.json + index.json)        ← 계보(lineage)가 사는 재료
#   ② 관측 시계열(observe/series.jsonl 한 줄 + 사이드카 7개)          ← 계기판·신호등이 읽는 재료
#   ③ 배포 복사본(altaiya/knowledge, robocopy /MIR)                  ← Railway 가 클론하는 저장소 안의 스냅샷
# 기획(docs/plan/atom-observatory_260907.html §분자 관측, 결정 2026-09-07): 스케줄러는 두지 않는다 —
# 적재를 돈 사람이 같은 자리에서 이 스크립트를 한 번 더 실행한다. 빠뜨리면 관측소 신호 `stale-snapshot` 이 알려준다.
#
# 이 파일은 반드시 **BOM 있는 UTF-8** 로 저장한다. Windows PowerShell 5.1 은 BOM 이 없으면 .ps1 을 cp949 로
# 읽어 한글·기호가 깨지고 파서가 죽는다("Unexpected token") — altaiya/run.ps1 과 같은 이유.
#
# 단계(어느 단계가 실패하면 그 자리에서 멈추고 뒤 단계는 돌지 않는다):
#   1/4  python -m geoguesshelper.molecule embed             증분 임베딩(HF 캐시만, GPU 있으면 수 초)      -SkipEmbed 로 생략
#   2/4  python -m geoguesshelper.molecule build --pct 0.5   새 스냅샷 + molecule/index.json (-Pct 로 변경)
#   3/4  python -m geoguesshelper.observe build --sweep      observe/ 사이드카 + series 한 줄 + 지속성(sweep)
#   4/4  altaiya\sync-knowledge.ps1                           docs/knowledge → altaiya/knowledge (npz·샌드박스 제외)  -SkipSync 로 생략
#
# 시스템 Python(numpy·networkx·scikit-learn·torch·sentence_transformers 가 있는 3.13)이 필요하다 — uv 가상환경에는 없다.
# 사용:  powershell -ExecutionPolicy Bypass -File snapshot-knowledge.ps1 [-SkipEmbed] [-SkipSync] [-Pct 0.5]
param(
    [switch]$SkipEmbed,
    [switch]$SkipSync,
    [double]$Pct = 0.5
)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path      # ...\GeoGuessHelper (이 파일이 루트에 있다)
Set-Location $root

$env:PYTHONUTF8 = "1"          # 콘솔 cp949 에서도 한글 출력이 깨지지 않게
$env:HF_HUB_OFFLINE = "1"      # 회사망 TLS 가로채기 — embed 는 HF 캐시만 쓴다
$env:PYTHONPATH = "src"        # 시스템 Python 이 본체 패키지를 찾도록(editable 설치 아님)

Write-Host ""
Write-Host "  == snapshot-knowledge — 분자 · 관측 · 배포본 세 겹을 고정한다 ==" -ForegroundColor Cyan
Write-Host ("     root {0}" -f $root) -ForegroundColor DarkGray

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "  ! python 을 찾을 수 없다 — 시스템 Python(3.13) 이 PATH 에 있어야 한다." -ForegroundColor Red
    exit 1
}
# numpy·networkx 가 없는 python(uv 환경 등)이면 embed/build 가 안내 후 종료한다 — 여기서 먼저 걸러 이유를 말해 준다.
python -c "import importlib.util as u, sys; sys.exit(0 if all(u.find_spec(m) for m in ('numpy', 'networkx')) else 3)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ! 이 python 에 numpy/networkx 가 없다 — uv 가상환경이 아니라 시스템 Python 으로 실행해야 한다." -ForegroundColor Red
    Write-Host ("    python = {0}" -f (Get-Command python).Source) -ForegroundColor DarkYellow
    exit 1
}

function Invoke-Step([string]$Label, [scriptblock]$Body) {
    Write-Host ""
    Write-Host ("  -- {0}" -f $Label) -ForegroundColor Cyan
    $t0 = Get-Date
    & $Body
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host ("  ! 실패: {0} (exit {1}) — 여기서 멈춘다. 뒤 단계는 실행하지 않았다." -f $Label, $LASTEXITCODE) -ForegroundColor Red
        exit $LASTEXITCODE
    }
    Write-Host ("  ok  {0}  ({1:N1}s)" -f $Label, ((Get-Date) - $t0).TotalSeconds) -ForegroundColor Green
}

if ($SkipEmbed) {
    Write-Host ""
    Write-Host "  -- 1/4 molecule embed — 생략(-SkipEmbed). 새 원자가 있으면 임베딩이 없어 분자·이웃에서 빠진다." -ForegroundColor DarkYellow
} else {
    Invoke-Step "1/4 molecule embed" { python -m geoguesshelper.molecule embed }
}

Invoke-Step ("2/4 molecule build --pct {0}" -f $Pct) { python -m geoguesshelper.molecule build --pct $Pct }

Invoke-Step "3/4 observe build --sweep" { python -m geoguesshelper.observe build --sweep }

if ($SkipSync) {
    Write-Host ""
    Write-Host "  -- 4/4 sync-knowledge — 생략(-SkipSync). 배포본(altaiya/knowledge)은 갱신하지 않았다." -ForegroundColor DarkYellow
} else {
    $sync = Join-Path $root "altaiya\sync-knowledge.ps1"
    # 같은 호스트의 새 프로세스로 돈다 — 그 스크립트의 `exit 1` 이 이 셸을 죽이지 않고 $LASTEXITCODE 로 돌아온다.
    Invoke-Step "4/4 sync-knowledge (docs/knowledge -> altaiya/knowledge)" { & powershell -NoProfile -ExecutionPolicy Bypass -File $sync }
}

Write-Host ""
Write-Host "  완료 — 스냅샷 세 겹 고정. 관측소: altaiya/run.ps1 → observe.html · 요약: docs/knowledge/observe/README.md" -ForegroundColor Green
if (-not $SkipSync) {
    Write-Host "  다음: cd altaiya; git add knowledge; git commit; git push  (Railway 가 자동 재배포)" -ForegroundColor DarkGray
}
exit 0
