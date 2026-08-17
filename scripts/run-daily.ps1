$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$LogDir = Join-Path $Root "data"
$Log = Join-Path $LogDir "daily.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
if (-not (Test-Path $VenvPython)) {
    & (Join-Path $PSScriptRoot "start.ps1")
}
Set-Location $Root
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $Log -Value "[$stamp] collect start"
& $VenvPython -m star_digest collect
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $Log -Value "[$stamp] collect exit $LASTEXITCODE"
