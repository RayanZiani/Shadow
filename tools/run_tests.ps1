<#
.SYNOPSIS
    Lance TOUS les tests du dépôt, service par service (équivalent Windows de
    `tools/run_tests.sh`).

.DESCRIPTION
    .\tools\run_tests.ps1          # tout
    .\tools\run_tests.ps1 -Fast    # sans l'entraînement/validation ML

    Prérequis : un venv à la racine (voir README « Tests ») et, pour le noyau
    cryptographique, cargo (rustup). Le venv peut être surchargé :

        $env:PYTHON = "C:\Python312\python.exe"

    Le script affiche un récapitulatif et sort avec :
        0  tout est vert
        1  au moins une suite a échoué
        2  le venv (ou l'interpréteur Python) est introuvable

    Pourquoi service par service (et pas `pytest services/`) : chaque service
    expose un `main.py`. Lancés dans le même processus, Python résoudrait
    `import main` vers le premier module homonyme importé, et des tests
    échoueraient pour une raison sans rapport avec le code testé. Ce script
    exécute donc chaque suite dans son propre dossier, comme le ferait la CI.

    L'ordre des suites est celui de `run_tests.sh` : les deux scripts doivent
    rester interchangeables.

.NOTES
    Windows PowerShell 5.1 et PowerShell 7+. Aucun module externe.
#>
[CmdletBinding()]
param(
    # Sauter la vérification des artefacts ML (`train.py --check`).
    [switch]$Fast
)

# Les accents des messages et la sortie de Python doivent passer en UTF-8.
# Sans cela, la console cp1252 (Windows FR) casse les « é » et les flèches.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {
    # Hôte sans console (CI, redirection) : on continue, l'UTF-8 est déjà posé
    # dans l'environnement des processus enfants.
}

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot

# ---------------------------------------------------------------------------
# Interpréteur Python
# ---------------------------------------------------------------------------
if ($env:PYTHON) {
    $Python = $env:PYTHON
} else {
    $Python = Join-Path $Root '.venv\Scripts\python.exe'
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    Write-Host "Python introuvable : $Python" -ForegroundColor Red
    Write-Host ""
    Write-Host "Créez le venv à la racine du dépôt :"
    Write-Host "    python -m venv .venv"
    Write-Host "    .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt"
    Write-Host ""
    Write-Host "Ou pointez un interpréteur existant :  `$env:PYTHON = 'C:\...\python.exe'"
    exit 2
}

# ---------------------------------------------------------------------------
# Suites : nom lisible, dossier d'exécution, arguments
# ---------------------------------------------------------------------------
# `Cargo` et `Train` sont traités à part : leur présence dépend du poste.
$Suites = @(
    @{ Name = "remediation-engine (bus d'états)";                     Dir = 'services/remediation-engine'; Args = @('-m', 'pytest', 'test_state_bus.py', '-q') },
    @{ Name = "remediation-engine (flux d'incident)";                 Dir = 'services/remediation-engine'; Args = @('test_incident_flow.py') },
    @{ Name = "remediation-engine (etat terminal garanti)";           Dir = 'services/remediation-engine'; Args = @('-m', 'pytest', 'test_incident_flow.py', '-q') },
    @{ Name = "remediation-engine (ingestion SIEM)";                  Dir = 'services/remediation-engine'; Args = @('-m', 'pytest', 'test_alert_ingest.py', '-q') },
    @{ Name = "ml-detector (features & anti-rebond)";                 Dir = 'services/ml-detector';        Args = @('-m', 'pytest', 'test_detector.py', '-q') },
    @{ Name = "payment-api (modèle de menace)";                       Dir = 'services/payment-api';        Args = @('-m', 'pytest', 'test_payment_api.py', '-q') },
    @{ Name = "payment-api (registre persistant)";                    Dir = 'services/payment-api';        Args = @('-m', 'pytest', 'test_registry.py', '-q') },
    @{ Name = "shadow-api (honeypot)";                                Dir = 'services/shadow-api';         Args = @('-m', 'pytest', 'test_shadow_api.py', '-q') },
    @{ Name = "auth-service (gestion de la clé JWT)";                 Dir = 'services/auth-service';       Args = @('-m', 'pytest', 'test_auth_service.py', '-q') },
    @{ Name = "backoffice (authentification visage + habilitations)"; Dir = 'services/backoffice';         Args = @('-m', 'pytest', 'test_backoffice.py', '-q') },
    @{ Name = "shop-api (boutique, panier, paiement, historique)";    Dir = 'services/shop-api';           Args = @('-m', 'pytest', 'test_shop_api.py', '-q') },
    @{ Name = "shop-api (dialecte PostgreSQL)";                       Dir = 'services/shop-api';           Args = @('-m', 'pytest', 'test_postgres_dialect.py', '-q') },
    @{ Name = "rag-assistant (indexation et sources)";                Dir = 'services/rag-assistant';      Args = @('-m', 'pytest', 'test_rag.py', '-q') },
    @{ Name = "waf (déception active)";                               Dir = 'services/waf';                Args = @('-m', 'pytest', 'test_waf.py', '-q') },
    @{ Name = "hardware (contrat série)";                             Dir = 'hardware/bridge';             Args = @('-m', 'pytest', 'test_hardware_bridge.py', '-q') },
    @{ Name = "encodage des fichiers livrés (aucun mojibake)";        Dir = '.';                           Args = @('tools/check_encoding.py') },
    @{ Name = "cohérence statique de la pile (compose, volumes, tables)"; Dir = '.';                        Args = @('tools/check_compose.py') }
)

$Cargo = Get-Command cargo -CommandType Application -ErrorAction SilentlyContinue
$HasCargo = $null -ne $Cargo

$script:Passed = New-Object System.Collections.Generic.List[string]
$script:Failed = New-Object System.Collections.Generic.List[string]
$script:Skipped = New-Object System.Collections.Generic.List[string]
$script:Total = $Suites.Count + 1  # + test_crypto_flow.py (intégration HTTP)
if ($HasCargo) { $script:Total++ }
if (-not $Fast) { $script:Total++ }
$script:Index = 0

function Invoke-Suite {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        # N'afficher la sortie qu'en cas d'échec (contrôles silencieux).
        [switch]$Quiet,
        # Code(s) de sortie à considérer comme « ignoré » plutôt qu'en échec.
        # pytest renvoie 5 quand aucun test n'a tourné : c'est exactement ce
        # que fait test_crypto_flow.py quand la pile n'est pas démarrée
        # (skip au niveau module). Ce n'est pas une régression.
        [int[]]$SkipCode = @()
    )

    $script:Index++
    Write-Host ""
    Write-Host ("=== [{0}/{1}] {2}" -f $script:Index, $script:Total, $Name) -ForegroundColor Cyan

    $workDir = Join-Path $Root $Directory
    if (-not (Test-Path -LiteralPath $workDir -PathType Container)) {
        Write-Host ("    ÉCHEC : dossier absent — {0}" -f $workDir) -ForegroundColor Red
        $script:Failed.Add($Name)
        return
    }

    # Le script lancé doit exister : mieux vaut un message net qu'un
    # « can't open file » noyé dans la sortie.
    $entry = $null
    foreach ($arg in $Arguments) {
        if ($arg -like '*.py') { $entry = $arg; break }
    }
    if ($entry -and -not (Test-Path -LiteralPath (Join-Path $workDir $entry) -PathType Leaf)) {
        Write-Host ("    ÉCHEC : fichier de test absent — {0}" -f (Join-Path $Directory $entry)) -ForegroundColor Red
        $script:Failed.Add($Name)
        return
    }

    $code = 1
    Push-Location -LiteralPath $workDir
    try {
        if ($Quiet) {
            # Sortie capturée : réaffichée seulement si le contrôle échoue,
            # pour ne pas noyer le récapitulatif.
            $output = & $Python @Arguments 2>&1
            $code = $LASTEXITCODE
            if ($null -eq $code) { $code = 1 }
            if ($code -ne 0) {
                foreach ($line in $output) { Write-Host ("    {0}" -f $line) }
            }
        } else {
            & $Python @Arguments
            $code = $LASTEXITCODE
            if ($null -eq $code) { $code = 1 }
        }
    } catch {
        Write-Host ("    ÉCHEC : {0}" -f $_.Exception.Message) -ForegroundColor Red
        $code = 1
    } finally {
        Pop-Location
    }

    if ($code -eq 0) {
        Write-Host "    OK" -ForegroundColor Green
        $script:Passed.Add($Name)
    } elseif ($SkipCode -contains $code) {
        Write-Host ("    IGNORÉ (code {0} : aucun test exécuté, pile absente ?)" -f $code) -ForegroundColor Yellow
        $script:Skipped.Add($Name)
    } else {
        Write-Host ("    ÉCHEC (code {0})" -f $code) -ForegroundColor Red
        $script:Failed.Add($Name)
    }
}

# ---------------------------------------------------------------------------
# Exécution
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Python : $Python" -ForegroundColor DarkGray
Write-Host ("Mode   : {0}" -f $(if ($Fast) { 'rapide (sans train.py --check)' } else { 'complet' })) -ForegroundColor DarkGray

foreach ($suite in $Suites) {
    Invoke-Suite -Name $suite.Name -Directory $suite.Dir -Arguments $suite.Args
}

# --- Rust : seulement si la toolchain est là -------------------------------
if ($HasCargo) {
    Invoke-Suite -Name "monero-crypto-core (Rust, unitaires)" `
        -Directory 'services/monero-crypto-core' `
        -Arguments @('test', '--release', '-q')
} else {
    Write-Host ""
    Write-Host "cargo absent : tests Rust ignorés (installez rustup)" -ForegroundColor Yellow
}

# --- Intégration HTTP : se déclare ignorée si la pile n'est pas démarrée ---
Invoke-Suite -Name "monero-crypto-core (bLSAG + audit, HTTP)" `
    -Directory 'services/monero-crypto-core' `
    -Arguments @('-m', 'pytest', 'test_crypto_flow.py', '-q') `
    -SkipCode @(5)

# --- Artefacts ML : contrôle silencieux, bruyant en cas d'échec ------------
if (-not $Fast) {
    Invoke-Suite -Name "ml-detector (artefacts présents)" `
        -Directory 'services/ml-detector' `
        -Arguments @('train.py', '--check') `
        -Quiet
}

# ---------------------------------------------------------------------------
# Récapitulatif
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Récapitulatif ===" -ForegroundColor Cyan
foreach ($name in $script:Passed) {
    Write-Host ("  {0,-6} {1}" -f 'OK', $name) -ForegroundColor Green
}
foreach ($name in $script:Skipped) {
    Write-Host ("  {0,-6} {1}" -f 'IGNORÉ', $name) -ForegroundColor Yellow
}
foreach ($name in $script:Failed) {
    Write-Host ("  {0,-6} {1}" -f 'ÉCHEC', $name) -ForegroundColor Red
}

$ok = $script:Passed.Count
$ko = $script:Failed.Count
$ignored = $script:Skipped.Count
Write-Host ""
Write-Host ("Total : {0} suite(s) — {1} OK, {2} en échec, {3} ignorée(s)" -f $script:Total, $ok, $ko, $ignored)

if ($ko -gt 0) {
    Write-Host "Tout n'est pas vert." -ForegroundColor Red
    exit 1
}
Write-Host "Tout est vert." -ForegroundColor Green
exit 0
