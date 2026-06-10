# GM Capital - Full Automation Scheduler Registration
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { Write-Host "Python not found" -ForegroundColor Red; exit 1 }

$dir = $PSScriptRoot
$s5  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5)  -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 1)
$s10 = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -RestartCount 1 -RestartInterval (New-TimeSpan -Minutes 2)
$s30 = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -RestartCount 1 -RestartInterval (New-TimeSpan -Minutes 5)

# 1. Morning Briefing - daily 06:30
$a1 = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\morning_briefing.py`"" -WorkingDirectory $dir
$t1 = New-ScheduledTaskTrigger -Daily -At "06:30"
Register-ScheduledTask -TaskName "GMCapital_MorningBriefing" -Action $a1 -Trigger $t1 -Settings $s10 -RunLevel Highest -Force
Write-Host "  [OK] Morning Briefing - daily 06:30" -ForegroundColor Green

# 2. Daily Report - daily 18:00
$a2 = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\daily_report.py`"" -WorkingDirectory $dir
$t2 = New-ScheduledTaskTrigger -Daily -At "18:00"
Register-ScheduledTask -TaskName "GMCapital_DailyReport" -Action $a2 -Trigger $t2 -Settings $s5 -RunLevel Highest -Force
Write-Host "  [OK] Daily Report - daily 18:00" -ForegroundColor Green

# 3. Volume Scanner - every hour during US market (KST 23:00~06:00)
$a3 = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\volume_scanner.py`"" -WorkingDirectory $dir
$scanTimes = @("23:00","00:00","01:00","02:00","03:00","04:00","05:00","06:00")
$t3list = $scanTimes | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ }
Register-ScheduledTask -TaskName "GMCapital_VolumeScanner" -Action $a3 -Trigger $t3list -Settings $s10 -RunLevel Highest -Force
Write-Host "  [OK] Volume Scanner - daily 23:00~06:00 every hour" -ForegroundColor Green

# 4. Earnings Alert - daily 07:30
$a4 = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\earnings_alert.py`"" -WorkingDirectory $dir
$t4 = New-ScheduledTaskTrigger -Daily -At "07:30"
Register-ScheduledTask -TaskName "GMCapital_EarningsAlert" -Action $a4 -Trigger $t4 -Settings $s10 -RunLevel Highest -Force
Write-Host "  [OK] Earnings Alert - daily 07:30" -ForegroundColor Green

# 5. Macro Briefing - 22:30 (CPI/NFP/GDP: 08:30 ET = 21:30 KST+1h) + 04:00 (FOMC: 14:00 ET = 03:00 KST+1h)
# 08:30 KST 제거: 미국 기준 전날 밤이라 지표 발표 전 → 예고 기사만 잡혀 오류
$a5  = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\macro_briefing.py`"" -WorkingDirectory $dir
$t5a = New-ScheduledTaskTrigger -Daily -At "22:30"
$t5b = New-ScheduledTaskTrigger -Daily -At "04:00"
Register-ScheduledTask -TaskName "GMCapital_MacroBriefing" -Action $a5 -Trigger @($t5a,$t5b) -Settings $s10 -RunLevel Highest -Force
Write-Host "  [OK] Macro Briefing - daily 22:30 (CPI/NFP/GDP), 04:00 (FOMC)" -ForegroundColor Green

# 6. Research Report PDF - every 2 weeks on Sunday 08:00
$a6 = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\research_report.py`"" -WorkingDirectory $dir
$t6 = New-ScheduledTaskTrigger -Weekly -WeeksInterval 2 -DaysOfWeek Sunday -At "08:00"
Register-ScheduledTask -TaskName "GMCapital_ResearchReport" -Action $a6 -Trigger $t6 -Settings $s30 -RunLevel Highest -Force
Write-Host "  [OK] Research Report PDF - biweekly Sunday 08:00" -ForegroundColor Green

# 7. Weekly Portfolio Report - every Monday 07:00
$a7 = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\weekly_portfolio.py`"" -WorkingDirectory $dir
$t7 = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday -At "07:00"
Register-ScheduledTask -TaskName "GMCapital_WeeklyPortfolio" -Action $a7 -Trigger $t7 -Settings $s30 -RunLevel Highest -Force
Write-Host "  [OK] Weekly Portfolio - every Monday 07:00" -ForegroundColor Green

Write-Host ""
Write-Host "GM Capital Automation Setup Complete!" -ForegroundColor Cyan
Write-Host "  Daily  : 04:00 / 06:30 / 07:30 / 18:00 / 22:30 / 23:00~06:00(hourly)" -ForegroundColor White
Write-Host "  Weekly : Monday 07:00" -ForegroundColor White
Write-Host "  Biweekly: Sunday 08:00" -ForegroundColor White
