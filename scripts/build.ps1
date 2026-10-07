$ErrorActionPreference = 'Stop'
$Project = Split-Path -Parent $PSScriptRoot
Set-Location $Project
if (!(Test-Path '.venv\Scripts\python.exe')) {
    python -m venv .venv
}
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe scripts\fetch_models.py
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --onedir --name AutoFlow --runtime-hook scripts\qt_runtime_hook.py --add-data 'resources;resources' --collect-all maa main.py
Copy-Item THIRD_PARTY_NOTICES.md dist\AutoFlow\ -Force
Write-Host 'Package: dist\AutoFlow\AutoFlow.exe'
