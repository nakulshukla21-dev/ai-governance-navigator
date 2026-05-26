# Launch the AI Governance Navigator Streamlit app from this project only.
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$Streamlit = Join-Path $ProjectRoot ".venv\Scripts\streamlit.exe"
$App = Join-Path $ProjectRoot "app.py"

if (-not (Test-Path $Streamlit)) {
    Write-Error "Virtual environment not found. Run: python -m venv .venv && .\.venv\Scripts\pip install -r requirements.txt"
    exit 1
}

if (-not (Test-Path $App)) {
    Write-Error "app.py not found in $ProjectRoot"
    exit 1
}

Write-Host "Starting AI Governance Navigator at http://localhost:8502"
Write-Host "Project: $ProjectRoot"
& $Streamlit run $App --server.port 8502
