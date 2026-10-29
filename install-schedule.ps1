param([double]$Capital = 100000)
$ErrorActionPreference = 'Stop'
if ($Capital -lt 10000) { throw '总权益至少10000' }
$script = Join-Path $PSScriptRoot 'daily.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -Capital $Capital" -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '18:00'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 5)
Register-ScheduledTask -TaskName 'A-Quant-V1-Close' -Action $action -Trigger $trigger -Settings $settings -Description 'A-Quant: 同步收盘行情，先分析所有持仓再扫描新股票。需开机、联网及安装AKShare。' -Force
Write-Host '已创建本机工作日18:00任务。节假日由交易日历判断；账户总权益变更后请重新配置。'
