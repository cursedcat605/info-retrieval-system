<#
.SYNOPSIS
    常驻运行「选调生去向信息检索系统」Web 服务：单实例 + 崩溃自动重启。

.DESCRIPTION
    供开机自启（配合 scripts\serve-hidden.vbs）或手动后台运行使用。
    - 启动前先探测端口，已在监听则不重复启动
    - 服务进程意外退出时自动拉起（默认 5 秒后重试，不限次数）
    - 日志：logs\web-server.log（守护进程事件） / logs\web-server.out.log（服务输出）

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\serve.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\serve.ps1 -Status
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\serve.ps1 -Stop
#>
[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$Status,
    [int]$MaxRestarts = 0,             # 0 = 不限次数
    [int]$RestartDelaySeconds = 5
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$logDir   = Join-Path $root "logs"
$logFile  = Join-Path $logDir "web-server.log"
$outFile  = Join-Path $logDir "web-server.out.log"
$errFile  = Join-Path $logDir "web-server.err.log"
$stopFlag = Join-Path $logDir "web-server.stop"

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

function Write-Log {
    param([string]$Message)
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
}

function Get-Listener {
    param([int]$Port)
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

# 运行一次服务并阻塞至退出。
# 注意：不能用 `& python ... *>> file`——uvicorn 的日志走 stderr，
# 而 $ErrorActionPreference='Stop' 会把原生命令的 stderr 当成终止性错误直接中断。
# Start-Process 的重定向由 .NET 完成，完全不经过 PowerShell 的流体系，稳定可靠。
function Start-ServerOnce {
    param([string]$PythonExe, [string]$Root, [string]$OutFile, [string]$ErrFile)

    # 轮转上一轮输出，保留崩溃现场
    foreach ($f in @($OutFile, $ErrFile)) {
        if ((Test-Path -LiteralPath $f) -and (Get-Item -LiteralPath $f).Length -gt 0) {
            Move-Item -LiteralPath $f -Destination "$f.prev" -Force
        }
    }

    # 必须带 -Wait：Windows PowerShell 5.1 下只用 -PassThru（即使再调 WaitForExit()）
    # 读不到 ExitCode，会得到空值。
    $started = Get-Date
    $proc = Start-Process -FilePath $PythonExe `
        -ArgumentList @('-u', '-m', 'web.backend.main') `
        -WorkingDirectory $Root `
        -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $OutFile `
        -RedirectStandardError $ErrFile

    $seconds = [int]((Get-Date) - $started).TotalSeconds

    # 进程被外部强杀时 ExitCode 读不到（为 $null）；正常退出时才有值
    $code = $null
    try { $code = $proc.ExitCode } catch { $code = $null }

    return [pscustomobject]@{ ExitCode = $code; Seconds = $seconds }
}

# --- 定位解释器 ---
$python = (Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $python) { $python = "python" }

# --- 读取配置端口（失败回退 8000）---
$port = 8000
try {
    $out = & $python -c "from src.utils.config import get; print(int(get(['web','port'], 8000) or 8000))" 2>$null
    if ($out -and ("$out".Trim() -match '^\d+$')) { $port = [int]("$out".Trim()) }
} catch { }

$url = "http://127.0.0.1:$port"

# ================= 状态 =================
if ($Status) {
    $l = Get-Listener $port
    if ($l) { Write-Host "● 运行中   $url   (PID $($l.OwningProcess))" -ForegroundColor Green }
    else    { Write-Host "○ 未运行   $url" -ForegroundColor Yellow }
    if (Test-Path $stopFlag) {
        Write-Host "! 存在停止标记 logs\web-server.stop —— 开机自启不会拉起服务（删除该文件即恢复）" -ForegroundColor Yellow
    }
    if (Test-Path $logFile) {
        Write-Host ""
        Write-Host "--- 最近 12 行守护日志 ($logFile) ---"
        Get-Content -LiteralPath $logFile -Tail 12 -Encoding UTF8
    }
    if (Test-Path $errFile) {
        Write-Host ""
        Write-Host "--- 最近 8 行服务日志 ($errFile) ---"
        Get-Content -LiteralPath $errFile -Tail 8 -Encoding UTF8
    }
    exit 0
}

# ================= 停止 =================
if ($Stop) {
    New-Item -ItemType File -Path $stopFlag -Force | Out-Null
    $l = Get-Listener $port
    if ($l) {
        Write-Log "收到停止请求：结束端口 $port 上的进程 PID $($l.OwningProcess)"
        Stop-Process -Id $l.OwningProcess -Force -ErrorAction SilentlyContinue
    } else {
        Write-Log "收到停止请求：端口 $port 未在监听"
    }
    Write-Host "已停止服务，并写入 logs\web-server.stop（守护进程会在数秒内自行退出）" -ForegroundColor Yellow
    exit 0
}

# ================= 启动（常驻）=================
Remove-Item -LiteralPath $stopFlag -Force -ErrorAction SilentlyContinue

$existing = Get-Listener $port
if ($existing) {
    Write-Log "端口 $port 已被 PID $($existing.OwningProcess) 占用，本次不重复启动"
    Write-Host "服务已在运行：$url" -ForegroundColor Green
    exit 0
}

Write-Log "==== 守护进程启动：PID $PID，监听端口 $port，解释器 $python ===="

$round = 0
while ($true) {
    if (Test-Path $stopFlag) { Write-Log "检测到停止标记，守护进程退出"; break }

    $round++
    Write-Log "拉起服务（第 $round 次）：$python -u -m web.backend.main"
    $reason = "启动失败"
    $seconds = 0
    try {
        $res     = Start-ServerOnce -PythonExe $python -Root $root -OutFile $outFile -ErrFile $errFile
        $seconds = $res.Seconds
        if ($null -ne $res.ExitCode) {
            $reason = "退出码 $($res.ExitCode)"
            # -1 = 进程被强制结束（如 Stop-Process / 任务管理器），不是服务自身崩溃
            if ([int]$res.ExitCode -eq -1) { $reason = "退出码 -1（进程被强制结束）" }
        } else {
            $reason = "进程已消失（无法读取退出码）"
        }
    } catch {
        $reason = "启动异常：$($_.Exception.Message)"
    }

    Write-Log "服务进程退出（$reason，运行 $seconds 秒）"
    # 启动后很快就退出 = 大概率是启动失败/崩溃循环，醒目标注
    if ($seconds -lt 10) { Write-Log "!! 服务运行不足 10 秒即退出，疑似启动失败，请查看 $errFile" }

    if (Test-Path $stopFlag) { Write-Log "检测到停止标记，守护进程退出"; break }
    if ($MaxRestarts -gt 0 -and $round -ge $MaxRestarts) {
        Write-Log "已达最大重启次数 $MaxRestarts，守护进程退出"
        break
    }

    Start-Sleep -Seconds $RestartDelaySeconds
}
