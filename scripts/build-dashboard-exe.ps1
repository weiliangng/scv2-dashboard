[CmdletBinding()]
param(
    [string]$Python = ".\\.venv\\Scripts\\python.exe"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = if ([System.IO.Path]::IsPathRooted($Python)) {
    $Python
} else {
    Join-Path $repoRoot $Python
}
$scriptPath = Join-Path $repoRoot "src\\scv2_dashboard.py"
$outputPath = Join-Path $repoRoot "SCV2-Dashboard.exe"
$workPath = Join-Path $repoRoot ".build\\pyinstaller-work"
$distPath = Join-Path $repoRoot ".build\\pyinstaller-dist"
$specPath = Join-Path $repoRoot ".build\\pyinstaller-spec"

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Python environment not found: $pythonPath. Create .venv and install requirements-build.txt."
}

& $pythonPath -c "import numpy, pyqtgraph, PySide6, serial, PyInstaller"
if ($LASTEXITCODE -ne 0) {
    throw "Dashboard build dependencies are missing. Install requirements-build.txt."
}

Remove-Item -LiteralPath $workPath -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $distPath -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $specPath -Recurse -Force -ErrorAction SilentlyContinue

& $pythonPath -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "SCV2-Dashboard" `
    --workpath $workPath `
    --distpath $distPath `
    --specpath $specPath `
    $scriptPath
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed to build the dashboard."
}

Copy-Item -LiteralPath (Join-Path $distPath "SCV2-Dashboard.exe") -Destination $outputPath -Force
$selfTest = Start-Process -FilePath $outputPath -ArgumentList "--self-test" -Wait -PassThru -WindowStyle Hidden
if ($selfTest.ExitCode -ne 0) {
    throw "The packaged dashboard failed its self-test."
}

Write-Host "Portable dashboard created: $outputPath"
