param([double]$Capital = 100000)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& '.venv/Scripts/python.exe' -m backend.cli daily --capital $Capital
if ($LASTEXITCODE) { throw '行情同步或日报失败；请查看错误，不使用旧报告下单' }
