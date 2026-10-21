param([Parameter(Mandatory=$true)][string]$ProjectRoot)
$ErrorActionPreference = 'Stop'
try {
    $root = (Resolve-Path -LiteralPath $ProjectRoot).Path
    $base = $PSScriptRoot
    if (-not (Test-Path -LiteralPath (Join-Path $root '2-Run-Windows.bat'))) { throw '目标目录没有 2-Run-Windows.bat。请指定项目根目录。' }
    if (-not (Test-Path -LiteralPath (Join-Path $root 'pet\chat\ai_settings_page.py'))) { throw '目标目录没有 pet\chat\ai_settings_page.py，可能选错目录。' }
    $files = @('pet\chat\models.py', 'pet\chat\providers.py', 'pet\chat\ai_settings_page.py', 'pet\chat\settings_dialog.py', 'pet\autonomy.py')
    $backup = Join-Path $root ('_backup_api_protocol_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
    $done=@()
    New-Item -ItemType Directory -Path $backup -Force | Out-Null
    Write-Host ('安装目标: ' + $root)
    Write-Host ('原文件备份: ' + $backup)
    foreach ($rel in $files) {
        $from = Join-Path (Join-Path $base 'payload') $rel
        $to = Join-Path $root $rel
        $backupFile = Join-Path $backup $rel
        if (-not (Test-Path -LiteralPath $from)) { throw "缺少补丁文件 $rel" }
        if (-not (Test-Path -LiteralPath $to)) { throw "项目缺少原文件 $rel" }
        New-Item -ItemType Directory -Path (Split-Path -Parent $backupFile) -Force | Out-Null
        Copy-Item -LiteralPath $to -Destination $backupFile -Force
        $done += $rel
    }
    foreach ($rel in $files) {
        $from = Join-Path (Join-Path $base 'payload') $rel
        $to = Join-Path $root $rel
        Copy-Item -LiteralPath $from -Destination $to -Force
        if ((Get-FileHash -LiteralPath $from -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $to -Algorithm SHA256).Hash) {
           throw "校验失败 $rel"
        }
        Write-Host ('[OK] ' + $rel)
    }
    Write-Host '安装完成。请完全退出桌宠，重新通过目标目录中的 2-Run-Windows.bat 启动。' -ForegroundColor Green
    exit 0
} catch {
    Write-Host ('安装失败: ' + $_.Exception.Message) -ForegroundColor Red
    if ($backup -and (Test-Path -LiteralPath $backup)) {
        Write-Host '尝试恢复安装前文件...' -ForegroundColor Yellow
        foreach ($rel in $done) {
            $src = Join-Path $backup $rel
            $dst = Join-Path $root $rel
            if (Test-Path -LiteralPath $src) { Copy-Item -LiteralPath $src -Destination $dst -Force }
        }
    }
    exit 1
}
