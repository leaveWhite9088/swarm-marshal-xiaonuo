$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root

try {
    Write-Host "=== [1/4] Checking Python syntax (py_compile) ===" -ForegroundColor Cyan
    $pyFiles = Get-ChildItem -Recurse -Filter *.py -Path "$Root\src", "$Root\tools", "$Root\leaderboard", "$Root\tests"
    foreach ($f in $pyFiles) {
        python -m py_compile $f.FullName
        if ($LASTEXITCODE -ne 0) { throw "py_compile failed on $($f.FullName)" }
    }

    Write-Host "=== [2/4] Checking SpecLock2 Pi Harness JS syntax & Pi CLI probe ===" -ForegroundColor Cyan
    npm run check:js
    if ($LASTEXITCODE -ne 0) { throw "JS syntax check failed" }
    node src/marshal/speclock_harness/pi_worker_runner.mjs --probe
    if ($LASTEXITCODE -ne 0) { throw "Pi CLI probe failed" }

    Write-Host "=== [3/4] Running full unit + three-baseline E2E suite ===" -ForegroundColor Cyan
    python -m unittest discover -s tests -p "test_*.py" -v
    if ($LASTEXITCODE -ne 0) { throw "Unit/E2E test suite failed" }

    Write-Host "=== [4/4] Running strict repository isolation audit ===" -ForegroundColor Cyan
    python tools/isolation_audit.py --strict
    if ($LASTEXITCODE -ne 0) { throw "Repository isolation audit failed" }

    Write-Host "`nALL CHECKS PASSED! swarm-marshal-xiaonuo is ready." -ForegroundColor Green
} finally {
    Pop-Location
}
