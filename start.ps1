# 私人数据时光轴 - 一键启动
# 用法: .\start.ps1  [可选: -Port 8764]
param(
    [int]$Port = 8764
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$python = $null
if ($env:MIMO_PYTHON -and (Test-Path $env:MIMO_PYTHON)) {
    $python = $env:MIMO_PYTHON
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $python = "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $python = "py"
}

if (-not $python) {
    Write-Host "[错误] 未找到 Python。请安装 Python 3.10+ 后重试。" -ForegroundColor Red
    exit 1
}

Write-Host "==========================================" -ForegroundColor Green
Write-Host "  私人数据时光轴" -ForegroundColor Green
Write-Host "  默认地址: http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green

& $python server.py --port $Port
