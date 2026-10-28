# Builds Label Miner end to end:
#   manual -> dist\Label-Miner\ -> build\installer\Label-Miner-Setup.exe
#
# Run from anywhere:  powershell -File packaging\build.ps1
# One-time setup:     pip install -e .[ocr,gui,build]
#                     winget install --id JRSoftware.InnoSetup -e

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "1/3  Manual (screenshots + PDF)..." -ForegroundColor Cyan
# Opens the app for a minute to take screenshots; don't touch the window.
python docs\manual\make_manual.py
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Screenshots failed - building the manual from the previous screenshots"
    python docs\manual\make_manual.py --no-shots
    if ($LASTEXITCODE -ne 0) { throw "Manual failed" }
}

Write-Host "2/3  PyInstaller..." -ForegroundColor Cyan
python -m PyInstaller packaging\Label-Miner.spec --noconfirm --clean --workpath build\pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Write-Host "3/3  Inno Setup..." -ForegroundColor Cyan
$iscc = Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
& $iscc packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

Write-Host "Done: build\installer\Label-Miner-Setup.exe" -ForegroundColor Green
