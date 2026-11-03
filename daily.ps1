param([double]$Capital = 100000, [ValidateSet('tencent','eastmoney')][string]$Provider = 'tencent')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& '.venv/Scripts/python.exe' -m backend.cli daily --provider $Provider --capital $Capital
if ($LASTEXITCODE) { throw '行情同步或日报失败；请查看错误，不使用旧报告下单' }
