<#
.SYNOPSIS
    用 Cloudflare 快速隧道（Quick Tunnel）把本机 Web 服务临时发布到公网，手机/外网可直接访问。

.DESCRIPTION
    免账号、免域名、免备案，零配置。
    - 启动前先探测本机 Web 服务是否在监听，未启动会给出提示
    - 单实例：已有 cloudflared 在跑则不重复启动
    - 自动抓取本次分配的公网地址，写入 logs\tunnel-url.txt（随时用 -Url 查看）
    - 隧道进程意外退出时自动拉起（默认 5 秒后重试，不限次数）
    - 日志：logs\tunnel.log（守护进程事件） / logs\tunnel.out.log、tunnel.err.log（cloudflared 原始输出）

.NOTES
    快速隧道每次启动都会分配一个新的随机域名，旧地址立即失效。
    这不是给生产环境用的方案，适合临时演示；长期使用请改用命名隧道。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tunnel.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tunnel.ps1 -Url
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tunnel.ps1 -Status
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tunnel.ps1 -Stop
#>
[CmdletBinding()]
param(
    [switch]$Stop,                     # 停止隧道（守护进程随之退出）
    [switch]$Status,                   # 查看运行状态与当前公网地址
    # 坑：PowerShell 变量名不区分大小写，这里若直接叫 $Url，脚本内的
    # `$url = <抓到的地址>` 就会命中这个 [switch] 参数并抛类型转换错误，
    # 导致隧道已经建好、守护进程却当场退出（留下孤儿 cloudflared）。
    # 因此参数改名为 $ShowUrl，仅保留 -Url 作为对外的别名。
    [Alias("Url")][switch]$ShowUrl,    # 只打印当前公网地址（方便脚本取值）
    [int]$RestartDelaySeconds = 5,
    [int]$UrlWaitSeconds = 60          # 等待分配域名的最长时间
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$logDir   = Join-Path $root "logs"
$logFile  = Join-Path $logDir "tunnel.log"
$outFile  = Join-Path $logDir "tunnel.out.log"
$errFile  = Join-Path $logDir "tunnel.err.log"
$urlFile  = Join-Path $logDir "tunnel-url.txt"
$stopFlag = Join-Path $logDir "tunnel.stop"

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

function Write-Log {
    param([string]$Message)
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
}

# 从 cloudflared 输出里抓快速隧道地址。
# 输出形如：
#   INF |  Your quick Tunnel has been created! Visit it at ...  |
#   INF |  https://<随机词>-<随机词>-....trycloudflare.com      |
function Get-TunnelUrl {
    foreach ($f in @($errFile, $outFile)) {
        if (Test-Path -LiteralPath $f) {
            $txt = Get-Content -LiteralPath $f -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
            if ($txt) {
                $m = [regex]::Match($txt, 'https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com')
                if ($m.Success) { return $m.Value }
            }
        }
    }
    return $null
}

# 定位 cloudflared.exe：优先用本机用户目录下的独立副本（免安装下载的那种），
# 其次回退到 PATH（例如 winget / scoop / choco 装的）。
function Resolve-Cloudflared {
    $local = Join-Path $env:LOCALAPPDATA "cloudflared\cloudflared.exe"
    if (Test-Path -LiteralPath $local) { return $local }
    $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    return $null
}

$cloudflared = Resolve-Cloudflared

# --- 读取配置端口（失败回退 8000）---
$port = 8000
$python = (Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if ($python) {
    try {
        $out = & $python -c "from src.utils.config import get; print(int(get(['web','port'], 8000) or 8000))" 2>$null
        if ($out -and ("$out".Trim() -match '^\d+$')) { $port = [int]("$out".Trim()) }
    } catch { }
}

$target = "http://127.0.0.1:$port"
$procs  = @(Get-Process cloudflared -ErrorAction SilentlyContinue)

# ================= 仅查看地址 =================
if ($ShowUrl) {
    if (Test-Path -LiteralPath $urlFile) { (Get-Content -LiteralPath $urlFile -Raw -Encoding UTF8).Trim() }
    else { Write-Host "尚无地址（隧道未启动过）" -ForegroundColor Yellow }
    exit 0
}

# ================= 状态 =================
if ($Status) {
    if ($procs.Count -gt 0) {
        Write-Host "● 隧道运行中   目标 $target   (PID $($procs[0].Id))" -ForegroundColor Green
    } else {
        Write-Host "○ 隧道未运行   目标 $target" -ForegroundColor Yellow
    }

    if (Test-Path -LiteralPath $urlFile) {
        Write-Host ""
        Write-Host "公网地址：" -NoNewline
        Write-Host " $((Get-Content -LiteralPath $urlFile -Raw -Encoding UTF8).Trim())" -ForegroundColor Cyan
        Write-Host "（快速隧道地址仅本次运行有效，重启后会变化）" -ForegroundColor DarkGray
    }

    if (-not $cloudflared) {
        Write-Host "! 找不到 cloudflared.exe，请先安装" -ForegroundColor Red
    }
    if (Test-Path $stopFlag) {
        Write-Host "! 存在停止标记 logs\tunnel.stop —— 删除该文件后才能再次启动隧道" -ForegroundColor Yellow
    }

    if (Test-Path $logFile) {
        Write-Host ""
        Write-Host "--- 最近 12 行守护日志 ($logFile) ---"
        Get-Content -LiteralPath $logFile -Tail 12 -Encoding UTF8
    }
    exit 0
}

if (-not $cloudflared) {
    Write-Host "找不到 cloudflared.exe。" -ForegroundColor Red
    Write-Host "安装方式：从 https://github.com/cloudflare/cloudflared/releases/latest 下载" -ForegroundColor Yellow
    Write-Host "  cloudflared-windows-amd64.exe，重命名为 cloudflared.exe 放到" -ForegroundColor Yellow
    Write-Host "  $env:LOCALAPPDATA\cloudflared\ 即可（无需管理员权限）" -ForegroundColor Yellow
    exit 1
}

# ================= 停止 =================
if ($Stop) {
    New-Item -ItemType File -Path $stopFlag -Force | Out-Null
    if ($procs.Count -gt 0) {
        foreach ($p in $procs) {
            Write-Log "收到停止请求：结束 cloudflared PID $($p.Id)"
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    } else {
        Write-Log "收到停止请求：没有在运行的 cloudflared 进程"
    }
    Remove-Item -LiteralPath $urlFile -Force -ErrorAction SilentlyContinue
    Write-Host "已停止隧道，并写入 logs\tunnel.stop（守护进程会在数秒内自行退出）" -ForegroundColor Yellow
    exit 0
}

# ================= 启动（常驻）=================
Remove-Item -LiteralPath $stopFlag -Force -ErrorAction SilentlyContinue

if ($procs.Count -gt 0) {
    Write-Log "已有 cloudflared 在运行（PID $($procs[0].Id)），本次不重复启动"
    Write-Host "隧道已在运行（PID $($procs[0].Id)）" -ForegroundColor Green
    $u = Get-TunnelUrl
    if ($u) { Write-Host "公网地址：$u" -ForegroundColor Cyan }
    exit 0
}

# 提醒：隧道只有在本机服务活着的时候才有意义
$listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    Write-Log "警告：端口 $port 未在监听，先启动 Web 服务再访问隧道地址"
    Write-Host "! 端口 $port 当前没有服务在监听，请先运行 scripts\serve.ps1" -ForegroundColor Yellow
    Write-Host "  （隧道仍会建立，但要等服务起来后地址才可用）" -ForegroundColor DarkGray
}

Write-Log "启动隧道：$cloudflared tunnel --url $target"

$failCount = 0

while ($true) {
    if (Test-Path $stopFlag) { Write-Log "检测到停止标记，守护进程退出"; break }

    # 轮转上一轮输出，保留崩溃现场
    foreach ($f in @($outFile, $errFile)) {
        if ((Test-Path -LiteralPath $f) -and (Get-Item -LiteralPath $f).Length -gt 0) {
            Move-Item -LiteralPath $f -Destination "$f.prev" -Force
        }
    }
    Remove-Item -LiteralPath $urlFile -Force -ErrorAction SilentlyContinue

    $started = Get-Date

    # 不用 -Wait：需要一边跑一边从日志里抓公网地址
    $proc = Start-Process -FilePath $cloudflared `
        -ArgumentList @('tunnel', '--url', $target, '--no-autoupdate') `
        -WorkingDirectory $root `
        -NoNewWindow -PassThru `
        -RedirectStandardOutput $outFile `
        -RedirectStandardError $errFile

    # 等 cloudflared 把随机域名分配下来
    $deadline = (Get-Date).AddSeconds($UrlWaitSeconds)
    $tunnelUrl = $null
    while (-not $proc.HasExited -and -not $tunnelUrl -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        $tunnelUrl = Get-TunnelUrl
    }

    if ($tunnelUrl) {
        Set-Content -LiteralPath $urlFile -Value $tunnelUrl -Encoding UTF8 -NoNewline
        Write-Log "隧道就绪：$tunnelUrl"
        Write-Host ""
        Write-Host "  公网地址：$tunnelUrl" -ForegroundColor Cyan
        Write-Host "  （手机/外网可直接打开；本地址仅本次运行有效）" -ForegroundColor DarkGray
        Write-Host ""
    } elseif (-not $proc.HasExited) {
        Write-Log "警告：${UrlWaitSeconds}s 内未从日志中解析出公网地址，请查看 logs\tunnel.err.log"
    }

    # 阻塞等待隧道进程退出（start 时间统计）
    while (-not $proc.HasExited) {
        Start-Sleep -Seconds 1
        if (Test-Path $stopFlag) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            break
        }
    }

    if (Test-Path $stopFlag) { Write-Log "检测到停止标记，守护进程退出"; break }

    $seconds = [int]((Get-Date) - $started).TotalSeconds
    $code = $null
    try { $code = $proc.ExitCode } catch { $code = $null }
    $failCount++
    Write-Log "隧道进程退出（运行 ${seconds}s，ExitCode=$code），${RestartDelaySeconds}s 后重启"

    Start-Sleep -Seconds $RestartDelaySeconds
}

Remove-Item -LiteralPath $urlFile -Force -ErrorAction SilentlyContinue
Write-Log "守护进程结束"
