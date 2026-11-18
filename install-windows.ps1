# Installe Yuki pour l'utilisateur courant (sans droits administrateur).
#   powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
#   ... -Restart      relance Yuki
#   ... -Uninstall    supprime tout ce que ce script a cree
# Fichier volontairement en ASCII : PowerShell 5 lit mal l'UTF-8 sans BOM.
param([switch]$Restart, [switch]$Uninstall)
$ErrorActionPreference = "Stop"

$Repo = $PSScriptRoot
$Data = Join-Path $env:LOCALAPPDATA "Yuki"
$Venv = Join-Path $Data "venv"
$Pythonw = Join-Path $Venv "Scripts\pythonw.exe"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$SettingsKey = "HKCU:\Software\yuki"
$DefaultArgs = "--density 0.7 --pile 0.35 --melt 0.006 --over-windows 0.6 --over-max 0.1 --fps 30"

function Stop-Yuki {
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*yuki.py*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Start-Yuki {
    $command = (Get-ItemProperty -Path $RunKey -Name Yuki -ErrorAction Stop).Yuki
    $arguments = $command.Substring($Pythonw.Length + 2).Trim()
    Start-Process -FilePath $Pythonw -ArgumentList $arguments
}

if ($Uninstall) {
    Stop-Yuki
    Remove-ItemProperty -Path $RunKey -Name Yuki -ErrorAction SilentlyContinue
    if (Test-Path $SettingsKey) { Remove-Item -Path $SettingsKey -Recurse -Force }
    Start-Sleep -Milliseconds 500
    if (Test-Path $Data) { Remove-Item -Path $Data -Recurse -Force }
    Write-Host "Yuki est desinstalle."
    exit 0
}

if ($Restart) {
    Stop-Yuki
    Start-Sleep -Milliseconds 500
    Start-Yuki
    Write-Host "Yuki est relance."
    exit 0
}

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -m venv $Venv
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python -m venv $Venv
} else {
    Write-Host "Python 3 manque : installe-le depuis python.org, puis relance ce script."
    exit 1
}
if ($LASTEXITCODE -ne 0) { throw "Creation de l'environnement Python impossible." }

& (Join-Path $Venv "Scripts\python.exe") -m pip install --quiet --upgrade PyQt6
if ($LASTEXITCODE -ne 0) { throw "Installation de PyQt6 impossible." }

$YukiArgs = if ($env:YUKI_ARGS) { $env:YUKI_ARGS } else { $DefaultArgs }
$command = '"{0}" "{1}" {2}' -f $Pythonw, (Join-Path $Repo "yuki.py"), $YukiArgs
Set-ItemProperty -Path $RunKey -Name Yuki -Value $command

Stop-Yuki
Start-Sleep -Milliseconds 500
Start-Yuki

Write-Host "Yuki est installe : il demarre a l'ouverture de session et se met en pause sur batterie."
Write-Host "Journal : $(Join-Path $Data 'yuki.log')"
