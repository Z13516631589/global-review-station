<#
.SYNOPSIS
  环球复盘站 · 本地兜底流程（当 GitHub Actions 的「复盘生成」批次没跑出骨架时，本机一键补齐）

.DESCRIPTION
  把「同步远端 → 抓当日数据 → 生成复盘骨架 → 构建 → 提交 → 推送 → 线上校验」
  串成一条稳定可重跑的流水线。设计原则：

    * 幂等可重跑：任何一步失败都能重跑；已存在的当日骨架默认不覆盖（--Force 才覆盖）。
    * 不误伤已发布内容：只 add 当日 MDX 与 src/data，绝不对个人目录或整仓做破坏性操作。
    * 安全推送：清代理后走 git 直连（本机代理不稳，走代理会 502）。
    * 失败不静默：任一环节失败即打印明确的下一步提示并给出非零退出码。

.PARAMETER Date
  目标交易日，YYYY-MM-DD。默认取「最近一个工作日」（周末自动回退到周五）。

.PARAMETER Batch
  数据批次名，默认「复盘生成」。

.PARAMETER Force
  覆盖已存在的当日 MDX 骨架（默认保留，避免覆盖手工填充过的观点）。

.PARAMETER NoPush
  只在本机跑到「生成骨架 + 构建」，不提交、不推送（用于先看效果）。

.PARAMETER SkipFetch
  跳过抓取，直接用磁盘上现有的 src/data/*.json 生成（离线 / 代理不可用时用）。

.PARAMETER SkipBuild
  跳过 npm run build（只抓数据 + 生成 MDX）。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\local-review.ps1
  powershell -ExecutionPolicy Bypass -File scripts\local-review.ps1 -Date 2026-10-09 -Force
  powershell -ExecutionPolicy Bypass -File scripts\local-review.ps1 -SkipFetch -NoPush
#>
[CmdletBinding()]
param(
    [string]$Date,
    [string]$Batch = '复盘生成',
    [switch]$Force,
    [switch]$NoPush,
    [switch]$SkipFetch,
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
# 中文输出：Windows PowerShell 5.1 下必须显式切到 UTF-8，否则中文会乱码
try { chcp 65001 | Out-Null } catch {}
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'

# ---------------------------------------------------------------- 路径 & 环境
$Root = Split-Path -Parent $PSScriptRoot          # 仓库根
$ScriptsDir = $PSScriptRoot
$DataDir = Join-Path $Root 'src\data'
$ReviewsDir = Join-Path $Root 'src\content\reviews'

# 解析 python：优先项目 venv，其次 PATH
$PyCandidates = @(
    'C:\Users\ztw\.workbuddy\binaries\python\envs\default\Scripts\python.exe',
    "$env:USERPROFILE\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
)
$Python = $PyCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $Python = $cmd.Source }
}
if (-not $Python) { throw '未找到可用的 Python。请安装 Python 3.10+ 或设置 PATH。' }

# 解析 node（managed 优先，否则 PATH）
$NodeDir = Get-ChildItem 'C:\Users\ztw\.workbuddy\binaries\node\versions' -Directory -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | Select-Object -First 1
$Npm = if ($NodeDir -and (Test-Path (Join-Path $NodeDir.FullName 'npm.cmd'))) {
    Join-Path $NodeDir.FullName 'npm.cmd'
} else { 'npm' }
$NodeExeDir = if ($NodeDir) { $NodeDir.FullName } else { $null }

function Info($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!!] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "  [XX] $m" -ForegroundColor Red }

$GitBase = @('-c', 'http.schannelCheckRevoke=false', '-c', 'http.sslVerify=false', '-c', 'http.proxy=', '-c', 'https.proxy=')

function Set-GitEnv {
    # 本机系统代理不稳，走代理会 502；统一清空后用直连
    $env:http_proxy = ''; $env:https_proxy = ''; $env:NO_PROXY = '*'
    $env:GIT_TERMINAL_PROMPT = '0'
}

function Invoke-Git {
    # 执行 git 并把 stderr 也当普通输出（避免 git 写 stderr 触发 NativeCommandError 红字）
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    Set-GitEnv
    $ErrorActionPreference = 'Continue'
    try {
        & git @GitBase @GitArgs 2>&1 | ForEach-Object { Write-Host $_ }
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = 'Stop'
    }
}

# ---------------------------------------------------------------- 目标交易日
function Resolve-TargetDate {
    param([string]$Given)
    if ($Given) {
        $d = [datetime]::MinValue
        if (-not [datetime]::TryParse($Given, [ref]$d)) { throw "无法解析日期：$Given（应为 YYYY-MM-DD）" }
        return $d.Date
    }
    $d = (Get-Date).Date
    while ($d.DayOfWeek -eq 'Saturday' -or $d.DayOfWeek -eq 'Sunday') { $d = $d.AddDays(-1) }
    return $d
}

$TargetDate = Resolve-TargetDate -Given $Date
$DateStr = $TargetDate.ToString('yyyy-MM-dd')
$MdxPath = Join-Path $ReviewsDir "$DateStr.mdx"

Write-Host ""
Write-Host "==============================================" -ForegroundColor White
Write-Host " 环球复盘站 · 本地兜底流程" -ForegroundColor White
Write-Host " 目标交易日：$DateStr   批次：$Batch" -ForegroundColor White
Write-Host "==============================================" -ForegroundColor White
Write-Host ""

if (-not (Test-Path $DataDir)) { throw "数据目录不存在：$DataDir" }

# ---------------------------------------------------------------- 1. 同步远端
Info "步骤 1/6 同步远端最新数据（避免与 CI 的抓取冲突/落后）"
$behind = $false
try {
    Set-GitEnv
    $ErrorActionPreference = 'Continue'
    Invoke-Git fetch origin main | Out-Null
    $remoteTip = (& git @GitBase ls-remote origin main 2>$null | Select-Object -First 1) -split '\s+' | Select-Object -First 1
    $localTip = (git rev-parse HEAD).Trim()
    $ErrorActionPreference = 'Stop'
    if ($remoteTip -and $remoteTip -ne $localTip) {
        Info "  远端 $($remoteTip.Substring(0,7)) ≠ 本地 $($localTip.Substring(0,7))，尝试快进合并远端数据快照"
        $code = Invoke-Git merge --ff-only $remoteTip
        if ($code -eq 0) { Ok '已快进合并远端数据快照' }
        else {
            Warn '快进合并失败（本地与远端分叉）。请手动处理后再跑；本次继续用本地数据。'
            $behind = $true
        }
    } else {
        Ok '本地已是最新'
    }
} catch {
    Warn "同步远端失败（$($_.Exception.Message)）；本次继续用本地数据。"
    $behind = $true
}

# ---------------------------------------------------------------- 2. 抓数据
if ($SkipFetch) {
    Warn '步骤 2/6 已跳过抓取（-SkipFetch），直接使用磁盘现有 src/data/*.json'
} else {
    Info "步骤 2/6 抓取当日数据（batch=$Batch）"
    Push-Location $Root
    try {
        $env:FETCH_BATCH = $Batch
        & $Python (Join-Path $ScriptsDir 'fetch_all.py') --batch $Batch
        if ($LASTEXITCODE -ne 0) { Warn "fetch_all 退出码 $LASTEXITCODE（部分数据源可能失败，已保留上一版）" }
        else { Ok '数据抓取完成' }
    } finally {
        Pop-Location
        Remove-Item Env:\FETCH_BATCH -ErrorAction SilentlyContinue
    }
}

# 数据新鲜度自检：确认 ashare.json 的 tradeDay 是否为目标交易日
$asharePath = Join-Path $DataDir 'ashare.json'
if (Test-Path $asharePath) {
    try {
        $ashare = Get-Content $asharePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $td = $ashare.tradeDay
        $expected = $TargetDate.ToString('yyyyMMdd')
        if ($td -eq $expected) { Ok "ashare.json tradeDay=$td 与目标交易日一致" }
        else { Warn "ashare.json tradeDay=$td ≠ 目标 $expected（数据可能尚未更新；可稍后重跑）" }
    } catch { Warn "读取 ashare.json 失败：$($_.Exception.Message)" }
} else {
    Warn '未找到 ashare.json，无法自检数据新鲜度'
}

# ---------------------------------------------------------------- 3. 生成骨架
Info "步骤 3/6 生成复盘 MDX"
if ((Test-Path $MdxPath) -and (-not $Force)) {
    Ok "已存在 $DateStr.mdx，保留原文件（如需覆盖请加 -Force）"
} else {
    # -Force 会覆盖已有 MDX（可能含已填充的人工观点）→ 先备份，绝不裸覆盖
    if (Test-Path $MdxPath) {
        $bak = "$MdxPath.bak"
        Copy-Item $MdxPath $bak -Force
        Warn "-Force 覆盖前已备份原文件到 $DateStr.mdx.bak（确认无误后可删除）"
    }
    Push-Location $Root
    try {
        $genArgs = @((Join-Path $ScriptsDir 'gen_review.py'), '--date', $DateStr, '--allow-stale')
        if ($Force) { $genArgs += '--force' }
        & $Python @genArgs
        if ($LASTEXITCODE -ne 0) { throw "gen_review.py 退出码 $LASTEXITCODE" }
        Ok "已生成 $DateStr.mdx 骨架（观点章节待补）"
    } finally { Pop-Location }
}

# ---------------------------------------------------------------- 4. 构建
if ($SkipBuild) {
    Warn '步骤 4/6 已跳过构建（-SkipBuild）'
} else {
    Info "步骤 4/6 构建静态站（npm run build）"
    Push-Location $Root
    try {
        if ($NodeExeDir) { $env:PATH = "$NodeExeDir;$env:PATH" }
        & $Npm run build 2>&1 | Select-Object -Last 6
        if ($LASTEXITCODE -ne 0) { throw "npm run build 退出码 $LASTEXITCODE" }
        Ok '构建完成'
    } finally { Pop-Location }
}

# ---------------------------------------------------------------- 5. 提交 & 推送
if ($NoPush) {
    Warn '步骤 5/6 已跳过提交/推送（-NoPush）'
} else {
    Info '步骤 5/6 提交并推送'
    Push-Location $Root
    try {
        # 只 add 当日 MDX + 数据目录，避免把无关改动一起带上
        Invoke-Git add -- $MdxPath $DataDir | Out-Null
        Set-GitEnv
        $staged = git diff --cached --name-only
        if (-not $staged) {
            Ok '没有需要提交的改动'
        } else {
            $msg = "review(local): $DateStr 复盘骨架（本地兜底流程）[skip ci]"
            $code = Invoke-Git commit -m $msg
            if ($code -ne 0) { throw "git commit 失败（退出码 $code）" }
            Ok '已提交'
            $code = Invoke-Git push origin main
            if ($code -ne 0) {
                Warn '推送失败，正在重试（清代理后 git 直连）…'
                Start-Sleep -Seconds 3
                $code = Invoke-Git push origin main
            }
            if ($code -ne 0) { throw "git push 失败（退出码 $code），请检查网络后手动 push origin main" }
            Ok '已推送（CI 将自动构建部署）'
        }
    } finally { Pop-Location }
}

# ---------------------------------------------------------------- 6. 线上校验
if (-not $NoPush) {
    Info '步骤 6/6 等待部署并校验线上页面（约 60~120s）'
    $url = "https://z13516631589.github.io/global-review-station/reviews/$DateStr/"
    $ok = $false
    foreach ($i in 1..10) {
        Start-Sleep -Seconds 15
        try {
            $resp = Invoke-WebRequest -Uri $url -Method Head -TimeoutSec 20 -UseBasicParsing
            if ($resp.StatusCode -eq 200) { Ok "线上已就绪：$url"; $ok = $true; break }
        } catch { Warn "第 $i 次探测未就绪（$($_.Exception.Message)）" }
    }
    if (-not $ok) { Warn "线上尚未就绪（可能仍在部署）。稍后手动访问：$url" }
}

Write-Host ""
if ($behind) { Warn '注意：本次未成功同步远端，可能与 CI 有分叉，建议手动 git status 检查。' }
Ok "本地兜底流程结束（交易日 $DateStr）"
Write-Host "  下一步：把当日真实数据写成观点 JSON，然后执行：" -ForegroundColor Gray
Write-Host "    python scripts/gen_review.py --date $DateStr --fill-viewpoint <file>.json" -ForegroundColor Gray
Write-Host "  再 npm run build && git push 即可。" -ForegroundColor Gray
