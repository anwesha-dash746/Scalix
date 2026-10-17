<#
.SYNOPSIS
    One-command launcher for SCALIX: sets everything up on first use, then opens the dashboard.

.DESCRIPTION
    1. Checks that Java 17+ is installed.
    2. Builds the simulator jar with the Maven wrapper if it is missing (or with -Rebuild).
    3. Creates the Python environment (.venv) and installs requirements.txt on first use,
       and again whenever requirements.txt changes.
    4. Starts the dashboard and opens it in your browser.

.EXAMPLE
    .\run.ps1                 # set up if needed, then open the dashboard
    .\run.ps1 -Port 8600      # use another port
    .\run.ps1 -Rebuild        # rebuild the Java simulator first
    .\run.ps1 -Test           # run all Java and Python tests instead of launching
    .\run.ps1 -NoBrowser      # start the server without opening a browser tab

    If Windows blocks scripts, run:  powershell -ExecutionPolicy Bypass -File .\run.ps1
#>
param(
    [int]$Port = 8501,
    [switch]$Rebuild,
    [switch]$Test,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$Jar = Join-Path $PSScriptRoot "target\scalix-1.0.0-SNAPSHOT-all.jar"
$VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$Requirements = Join-Path $PSScriptRoot "requirements.txt"
$RequirementsStamp = Join-Path $PSScriptRoot ".venv\scalix-requirements.sha256"

function Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Fail($message) { Write-Host "ERROR: $message" -ForegroundColor Red; exit 1 }

# 1. Java -----------------------------------------------------------------------------
Step "Checking Java"
if (-not (Get-Command java -ErrorAction SilentlyContinue)) {
    Fail "Java was not found. Install a JDK 17 or newer (e.g. https://adoptium.net) and reopen PowerShell."
}
$javaVersion = (& cmd /c "java -version 2>&1" | Select-Object -First 1)
Write-Host "    $javaVersion"

# 2. Simulator jar ----------------------------------------------------------------------
if ($Rebuild -or -not (Test-Path $Jar)) {
    Step "Building the simulator (first run downloads Maven and dependencies; this can take a few minutes)"
    & "$PSScriptRoot\mvnw.cmd" -B -q package -DskipTests
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Jar)) { Fail "The simulator build failed. Run '.\mvnw.cmd -B package' to see details." }
} else {
    Step "Simulator already built"
}

# 3. Python environment -----------------------------------------------------------------
if (-not (Test-Path $VenvPython)) {
    Step "Creating the Python environment (.venv)"
    $pyExe = $null
    $pyArgs = @()
    if (Get-Command py -ErrorAction SilentlyContinue) { $pyExe = "py"; $pyArgs = @("-3") }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { $pyExe = "python" }
    if (-not $pyExe) { Fail "Python was not found. Install Python 3.11 or newer from https://www.python.org and reopen PowerShell." }
    & $pyExe @pyArgs -m venv .venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPython)) { Fail "Could not create the Python environment." }
}

$hash = (Get-FileHash $Requirements -Algorithm SHA256).Hash
$installed = if (Test-Path $RequirementsStamp) { (Get-Content $RequirementsStamp -Raw).Trim() } else { "" }
if ($hash -ne $installed) {
    Step "Installing Python packages (first run only, about a minute)"
    & $VenvPython -m pip install --disable-pip-version-check -q -r $Requirements
    if ($LASTEXITCODE -ne 0) { Fail "Installing the Python packages failed." }
    Set-Content -Path $RequirementsStamp -Value $hash -Encoding ascii
} else {
    Step "Python packages up to date"
}

# 4a. Tests -------------------------------------------------------------------------------
if ($Test) {
    Step "Running the Java tests"
    & "$PSScriptRoot\mvnw.cmd" -B -q test
    $javaOk = ($LASTEXITCODE -eq 0)
    Step "Running the Python tests"
    & $VenvPython -m pytest -q
    $pythonOk = ($LASTEXITCODE -eq 0)
    if ($javaOk -and $pythonOk) { Write-Host "All tests passed." -ForegroundColor Green; exit 0 }
    Fail "Some tests failed (Java ok: $javaOk, Python ok: $pythonOk)."
}

# 4b. Dashboard -----------------------------------------------------------------------------
$Url = "http://localhost:$Port"
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Fail "Port $Port is already in use. SCALIX may already be running: open $Url, or start another copy with .\run.ps1 -Port 8600"
}

if (-not $NoBrowser) {
    # Streamlit runs headless (no first-run prompts); open the browser once the server answers.
    Start-Job -ArgumentList $Url -ScriptBlock {
        param($u)
        for ($i = 0; $i -lt 90; $i++) {
            try {
                Invoke-WebRequest "$u/_stcore/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
                Start-Process $u
                return
            } catch { Start-Sleep -Seconds 1 }
        }
    } | Out-Null
}

Step "Starting the SCALIX dashboard at $Url  (press Ctrl+C to stop)"
& $VenvPython -m streamlit run dashboard\app.py --server.port $Port --server.headless true
