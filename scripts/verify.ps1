#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Synthetix UX icin tek komutluk yerel dogrulama: statik kalite kapilari +
    birlesik otomatik test paketi (backend/frontend/contract/E2E).

.DESCRIPTION
    Adimlar sirayla calisir; herhangi biri basarisiz olursa script ilk
    hatada durur ve non-zero exit code doner. E2E adimi kendi izole docker
    compose stack'ini (`compose.e2e.env`, ayri proje adi/portlar/veritabani)
    kurar ve is bittiginde -basarili/basarisiz fark etmeksizin- her zaman
    indirir (`try/finally`); gelistirme stack'i hicbir zaman etkilenmez.

.USAGE
    ./scripts/verify.ps1                 # tum adimlar
    ./scripts/verify.ps1 -SkipE2E        # E2E haric (daha hizli yerel dongu)
    ./scripts/verify.ps1 -OnlyBackend    # yalnizca backend adimlari
    ./scripts/verify.ps1 -OnlyFrontend   # yalnizca frontend adimlari
#>

[CmdletBinding()]
param(
    [switch]$SkipE2E,
    [switch]$OnlyBackend,
    [switch]$OnlyFrontend
)

# NOT: KASITLI olarak "Stop" DEGIL. PowerShell 5.1, harici komutlarin
# (docker/npm/pytest) stderr'e yazdigi HER SATIRI (ornegin docker compose'un
# rutin build ilerleme ciktisi - basari durumunda bile) "Stop" ile
# sonlandirici bir hataya cevirir. Basarisizlik tespiti bunun yerine her
# `Exec` cagrisindan sonra acikca `$LASTEXITCODE` kontrol edilerek yapilir.
$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$script:StepResults = [System.Collections.Generic.List[object]]::new()
$PlaywrightImage = "mcr.microsoft.com/playwright:v1.49.1-noble"

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Action
    )
    Write-Host ""
    Write-Host "==> $Name" -ForegroundColor Cyan
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        & $Action
        if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {
            throw "Adim non-zero exit code ile bitti: $LASTEXITCODE"
        }
        $sw.Stop()
        $script:StepResults.Add([pscustomobject]@{ Name = $Name; Status = "OK"; Seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1) })
        Write-Host "OK: $Name ($([math]::Round($sw.Elapsed.TotalSeconds, 1))s)" -ForegroundColor Green
    }
    catch {
        $sw.Stop()
        $script:StepResults.Add([pscustomobject]@{ Name = $Name; Status = "FAIL"; Seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1) })
        Write-Host "HATA: $Name basarisiz oldu." -ForegroundColor Red
        Write-Host $_.Exception.Message -ForegroundColor Red
        throw
    }
}

function Write-Summary {
    Write-Host ""
    Write-Host "===================== Ozet =====================" -ForegroundColor Cyan
    foreach ($result in $script:StepResults) {
        $color = if ($result.Status -eq "OK") { "Green" } else { "Red" }
        Write-Host ("{0,-45} {1,-6} {2,6}s" -f $result.Name, $result.Status, $result.Seconds) -ForegroundColor $color
    }
    $failed = $script:StepResults | Where-Object { $_.Status -ne "OK" }
    Write-Host "=================================================="
    if ($failed) {
        Write-Host "$($failed.Count) adim basarisiz." -ForegroundColor Red
    }
    else {
        Write-Host "Tum adimlar basarili ($($script:StepResults.Count)/$($script:StepResults.Count))." -ForegroundColor Green
    }
}

function Exec {
    param([string]$Command)
    Write-Host "  `$ $Command" -ForegroundColor DarkGray
    Invoke-Expression $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Komut basarisiz oldu (exit $LASTEXITCODE): $Command"
    }
}

function Invoke-E2E {
    $E2EProject = "synthetix-ux-e2e"
    $E2EComposeArgs = "--env-file compose.e2e.env -p $E2EProject -f compose.yaml"

    Invoke-Step "E2E: izole compose stack'i ayaga kaldiriliyor" {
        Exec "docker compose $E2EComposeArgs up -d --build"
    }

    Invoke-Step "E2E: veritabani migration'lari uygulaniyor" {
        Exec "docker compose $E2EComposeArgs exec -T backend alembic upgrade head"
    }

    try {
        Invoke-Step "E2E: Playwright bagimliliklari kuruluyor" {
            Exec "docker run --rm -v `"${RepoRoot}/e2e:/e2e`" -w /e2e $PlaywrightImage npm install"
        }

        Invoke-Step "E2E: Playwright testleri calistiriliyor" {
            # `--network` KULLANILMAZ: tarayici, frontend/backend'e Docker ic
            # servis adlariyla degil (bunlar farkli "site" sayilir ve
            # SameSite=Lax oturum cookie'lerini kirar), Docker Desktop'in
            # host gateway'i (`host.docker.internal`) uzerinden, gercek bir
            # host tarayicisiyla ayni sekilde erisir (bkz. compose.e2e.env).
            Exec "docker run --rm -e E2E_BASE_URL=http://host.docker.internal:5273 -v `"${RepoRoot}/e2e:/e2e`" -w /e2e $PlaywrightImage npx playwright test"
        }
    }
    finally {
        # Basari/basarisizlik fark etmeksizin izole E2E stack'i her zaman
        # temizlenir (ephemeral: dev veritabanina hicbir etkisi yoktur).
        Exec "docker compose $E2EComposeArgs down -v"
    }
}

$RunBackend = -not $OnlyFrontend
$RunFrontend = -not $OnlyBackend
$RunE2E = $RunBackend -and $RunFrontend -and -not $SkipE2E

try {
    Invoke-Step "Dev stack ayaga kaldiriliyor" {
        Exec "docker compose up -d --build db redis backend worker analyzer frontend"
    }

    if ($RunBackend) {
        Invoke-Step "Backend: dev bagimliliklari kuruluyor" {
            Exec "docker compose exec -T backend pip install -q --retries 5 --timeout 60 -r requirements-dev.txt"
        }

        Invoke-Step "Backend: ruff format --check" {
            Exec "docker compose exec -T backend ruff format --check app tests"
        }

        Invoke-Step "Backend: ruff check" {
            Exec "docker compose exec -T backend ruff check app tests"
        }

        Invoke-Step "Backend: mypy" {
            Exec "docker compose exec -T backend mypy app"
        }

        Invoke-Step "Backend: pytest (unit + integration + security, coverage)" {
            Exec 'docker compose exec -T backend pytest -m "unit or integration or security" --cov=app --cov-branch --cov-report=term-missing --cov-report=json -q'
        }

        Invoke-Step "Backend: kritik dosyalarda %100 branch coverage" {
            Exec "python scripts/check_critical_coverage.py"
        }

        Invoke-Step "Contract: frontend-backend sema uyumu" {
            Exec "python scripts/contract_check.py"
        }
    }

    if ($RunFrontend) {
        Invoke-Step "Frontend: lint" {
            Exec "docker compose exec -T frontend npm run lint"
        }

        Invoke-Step "Frontend: format check" {
            Exec "docker compose exec -T frontend npm run format:check"
        }

        Invoke-Step "Frontend: typecheck" {
            Exec "docker compose exec -T frontend npm run typecheck"
        }

        Invoke-Step "Frontend: test + coverage" {
            Exec "docker compose exec -T frontend npm run test:coverage"
        }

        Invoke-Step "Frontend: production build" {
            Exec "docker compose exec -T frontend npm run build"
        }
    }

    if ($RunE2E) {
        Invoke-E2E
    }

    Write-Summary
    exit 0
}
catch {
    Write-Summary
    exit 1
}
