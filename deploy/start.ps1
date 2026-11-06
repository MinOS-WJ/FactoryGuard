param(
    [ValidateSet("gui", "tray")]
    [string]$Mode = "gui",
    [string]$Url = "rtsp://localhost/live",
    [string]$Database = "data/factoryguard.db",
    [string]$Config = "config/factoryguard.json",
    [string]$MediaDir = "events-media",
    [string]$Python = "",
    [switch]$Check,
    [switch]$StrictCheck
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"
if ($StrictCheck) {
    $Check = $true
}

if (-not $Python) {
    $Python = $env:FACTORYGUARD_PYTHON
}
if (-not $Python) {
    $command = Get-Command python -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "Python was not found. Activate the factoryguard environment or pass -Python."
    }
    $Python = $command.Source
}

$entrypoint = Join-Path $root "src\main.py"
if (-not (Test-Path -LiteralPath $entrypoint)) {
    throw "Missing entry point: $entrypoint"
}

& $Python -c "import PySide6, numpy"
if ($LASTEXITCODE -ne 0) {
    throw "FactoryGuard runtime dependencies are missing. Run: python -m pip install -r requirements.txt"
}

if ($Check) {
    $healthArgs = @("health", "--db", $Database, "--media-dir", $MediaDir)
    if ($Url) {
        $healthArgs += @("--rtsp", $Url)
    }
    if ($StrictCheck) {
        $healthArgs += "--strict"
    }
    & $Python $entrypoint @healthArgs
    exit $LASTEXITCODE
}

& $Python $entrypoint $Mode --db $Database --config $Config
exit $LASTEXITCODE
