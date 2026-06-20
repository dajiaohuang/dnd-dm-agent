# Start dnd-dm-agent with all channels
# Usage:
#   .\scripts\start-all.ps1                  # full start (QR login if needed)
#   .\scripts\start-all.ps1 -Quick           # skip QR (saved login)
#   .\scripts\start-all.ps1 -NoQQ            # skip QQ entirely
#   .\scripts\start-all.ps1 -CpuOnly         # no GPU for embeddings
#   .\scripts\start-all.ps1 -RestartGateway  # kill + restart gateway
param([switch]$NoQQ, [switch]$Quick, [switch]$CpuOnly, [switch]$RestartGateway)

$ErrorActionPreference = "Stop"
$RepoRoot = (Get-Item .).FullName

Write-Host "=== dnd-dm-agent ===" -ForegroundColor Cyan

# ---------- NapCat QQ ----------
if (-not $NoQQ) {
    $LocalQQ = Get-ChildItem -Directory -Path $RepoRoot -Filter "*_localqq" |
        Select-Object -First 1
    if (-not $LocalQQ) {
        Write-Host "[!] No *_localqq directory. Run: .\scripts\setup-napcat.ps1" -ForegroundColor Red
        Write-Host "    Or skip QQ with: .\scripts\start-all.ps1 -NoQQ" -ForegroundColor Yellow
        exit 1
    }
    $LocalQQ = $LocalQQ.FullName

    $QQRunning = Get-Process -Name "QQ" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "*_localqq*" }

    if (-not $QQRunning) {
        if ($Quick) {
            Write-Host "[..] Quick-starting NapCat QQ (saved login)..." -ForegroundColor Yellow
            $QuickScript = Join-Path $LocalQQ "start-quick.ps1"
            Start-Process -FilePath "powershell" -ArgumentList "-File `"$QuickScript`"" -WindowStyle Minimized
        } else {
            Write-Host "[..] Starting NapCat QQ..." -ForegroundColor Yellow
            $MainScript = Join-Path $LocalQQ "start.ps1"
            Start-Process -FilePath "powershell" -ArgumentList "-File `"$MainScript`"" -WindowStyle Minimized
        }
        Write-Host "[OK] NapCat launched" -ForegroundColor Green
    } else {
        Write-Host "[OK] NapCat QQ already running" -ForegroundColor Green
    }
}

# ---------- nanobot gateway ----------
if ($RestartGateway) {
    Write-Host "[..] Stopping existing gateway..." -ForegroundColor Yellow
    Get-Process -Name "python" -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 2
}

$GwRunning = Get-Process -Name "python" -ErrorAction SilentlyContinue
if ($GwRunning) {
    Write-Host "[OK] Gateway already running (PID: $($GwRunning.Id))" -ForegroundColor Green
} else {
    Write-Host "[..] Starting nanobot gateway..." -ForegroundColor Yellow
    if (-not $CpuOnly) {
        $env:DND_EMBEDDING_DEVICE = "cuda"
        Write-Host "     GPU mode (BGE-M3 on CUDA)" -ForegroundColor DarkGray
    } else {
        Write-Host "     CPU mode" -ForegroundColor DarkGray
    }
    $GatewayJob = Start-Job -Name "nanobot-gateway" -ScriptBlock {
        param($Env_DND)
        $env:DND_EMBEDDING_DEVICE = $Env_DND
        nanobot gateway 2>&1
    } -ArgumentList $env:DND_EMBEDDING_DEVICE
    Write-Host "[OK] Gateway started (Job: $($GatewayJob.Id))" -ForegroundColor Green
}

Write-Host ""
Write-Host "=== Running ===" -ForegroundColor Cyan
Write-Host "WebUI:  http://127.0.0.1:18765" -ForegroundColor White
Write-Host "Health: http://127.0.0.1:18790/health" -ForegroundColor DarkGray
Write-Host "NapCat: http://127.0.0.1:6099" -ForegroundColor DarkGray
