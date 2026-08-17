$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = "C:\Users\43886\AppData\Local\Programs\Python\Python313\python.exe"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$Port = 8787
$Url = "http://127.0.0.1:$Port/"

Set-Location $Root

if (-not (Test-Path $VenvPython)) {
    if (-not (Test-Path $Python)) {
        $Python = (Get-Command py -ErrorAction Stop).Source
        & $Python -3.13 -m venv (Join-Path $Root ".venv")
    } else {
        & $Python -m venv (Join-Path $Root ".venv")
    }
}
$needInstall = $true
try {
    & $VenvPython -c "import fastapi, uvicorn, httpx, bs4, openai, dotenv" | Out-Null
    if ($LASTEXITCODE -eq 0) { $needInstall = $false }
} catch { $needInstall = $true }
if ($needInstall) {
    & $VenvPython -m pip install -q -r (Join-Path $Root "requirements.txt")
}

$inUse = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $inUse) {
    Start-Process -FilePath $VenvPython -ArgumentList @("-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "$Port") -WorkingDirectory $Root -WindowStyle Minimized
    $ready = $false
    foreach ($i in 1..40) {
        Start-Sleep -Milliseconds 400
        try {
            $res = Invoke-WebRequest -Uri "$Url`api/health" -UseBasicParsing -TimeoutSec 2
            if ($res.StatusCode -eq 200) { $ready = $true; break }
        } catch {}
    }
    if (-not $ready) {
        Write-Host "服务启动超时，请看最小化窗口里的报错。"
    }
}

Start-Process $Url
