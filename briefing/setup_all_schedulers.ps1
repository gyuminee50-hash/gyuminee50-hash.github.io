# GM Capital - Full Automation Scheduler Registration
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { Write-Host "Python not found" -ForegroundColor Red; exit 1 }

$dir = $PSScriptRoot
$s5  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5)  -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
$s10 = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -RestartCount 1 -RestartInterval (New-TimeSpan -Minutes 2) -StartWhenAvailable
$s30 = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -RestartCount 1 -RestartInterval (New-TimeSpan -Minutes 5) -StartWhenAvailable
$s0  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 0)    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) -StartWhenAvailable

# 1. Morning Briefing - daily 06:30
$a1 = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\morning_briefing.py`"" -WorkingDirectory $dir
$t1 = New-ScheduledTaskTrigger -Daily -At "06:30"
Register-ScheduledTask -TaskName "GMCapital_MorningBriefing" -Action $a1 -Trigger $t1 -Settings $s10 -Force
Write-Host "  [OK] Morning Briefing - daily 06:30" -ForegroundColor Green

# 2. Daily Report - daily 18:00
$a2 = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\daily_report.py`"" -WorkingDirectory $dir
$t2 = New-ScheduledTaskTrigger -Daily -At "18:00"
Register-ScheduledTask -TaskName "GMCapital_DailyReport" -Action $a2 -Trigger $t2 -Settings $s5 -Force
Write-Host "  [OK] Daily Report - daily 18:00" -ForegroundColor Green

# 3. Volume Scanner - every hour during US market (KST 23:00~06:00)
$a3 = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\volume_scanner.py`"" -WorkingDirectory $dir
$t3list = @("23:00","00:00","01:00","02:00","03:00","04:00","05:00","06:00") | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ }
Register-ScheduledTask -TaskName "GMCapital_VolumeScanner" -Action $a3 -Trigger $t3list -Settings $s10 -Force
Write-Host "  [OK] Volume Scanner - daily 23:00~06:00 every hour" -ForegroundColor Green

# 4. Earnings Alert - daily 07:30
$a4 = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\earnings_alert.py`"" -WorkingDirectory $dir
$t4 = New-ScheduledTaskTrigger -Daily -At "07:30"
Register-ScheduledTask -TaskName "GMCapital_EarningsAlert" -Action $a4 -Trigger $t4 -Settings $s10 -Force
Write-Host "  [OK] Earnings Alert - daily 07:30" -ForegroundColor Green

# 5. Macro Briefing - 22:30 + 04:00
$a5  = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\macro_briefing.py`"" -WorkingDirectory $dir
$t5a = New-ScheduledTaskTrigger -Daily -At "22:30"
$t5b = New-ScheduledTaskTrigger -Daily -At "04:00"
Register-ScheduledTask -TaskName "GMCapital_MacroBriefing" -Action $a5 -Trigger @($t5a,$t5b) -Settings $s10 -Force
Write-Host "  [OK] Macro Briefing - daily 22:30 + 04:00" -ForegroundColor Green

# 6. Research Report PDF - every 2 weeks on Sunday 08:00
$a6 = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\research_report.py`"" -WorkingDirectory $dir
$t6 = New-ScheduledTaskTrigger -Weekly -WeeksInterval 2 -DaysOfWeek Sunday -At "08:00"
Register-ScheduledTask -TaskName "GMCapital_ResearchReport" -Action $a6 -Trigger $t6 -Settings $s30 -Force
Write-Host "  [OK] Research Report PDF - biweekly Sunday 08:00" -ForegroundColor Green

# 7. Weekly Portfolio Report - every Monday 07:00
$a7 = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\weekly_portfolio.py`"" -WorkingDirectory $dir
$t7 = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday -At "07:00"
Register-ScheduledTask -TaskName "GMCapital_WeeklyPortfolio" -Action $a7 -Trigger $t7 -Settings $s30 -Force
Write-Host "  [OK] Weekly Portfolio - every Monday 07:00" -ForegroundColor Green

# 8. Investment Journal - run at logon (watchdog, always-on)
$a8 = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\investment_journal.py`"" -WorkingDirectory $dir
$t8 = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "GMCapital_InvestmentJournal" -Action $a8 -Trigger $t8 -Settings $s0 -Force
Write-Host "  [OK] Investment Journal - run at logon (always-on watchdog)" -ForegroundColor Green

# 9. 임시로 만든 Morning_0630 삭제 (중복 방지)
Unregister-ScheduledTask -TaskName "GMCapital_Morning_0630" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "  [OK] 임시 Morning_0630 태스크 제거" -ForegroundColor Yellow

# 10. DB 초기화 (최초 1회 — 이미 존재해도 무해)
Write-Host "  [DB] gmcapital.db 초기화 중..." -ForegroundColor Yellow
& $python "$dir\db_setup.py"
Write-Host "  [OK] DB 초기화 완료" -ForegroundColor Green

# 11. 결과추적 잡 - daily 23:30 (T+5 지난 pending 신호 채점)
$a11 = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\evaluate_signals.py`"" -WorkingDirectory $dir
$t11 = New-ScheduledTaskTrigger -Daily -At "23:30"
Register-ScheduledTask -TaskName "GMCapital_EvaluateSignals" -Action $a11 -Trigger $t11 -Settings $s10 -Force
Write-Host "  [OK] Evaluate Signals - daily 23:30" -ForegroundColor Green

# 12. 야간 컨설팅 잡 - daily 23:45
$a12 = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\consult_report.py`"" -WorkingDirectory $dir
$t12 = New-ScheduledTaskTrigger -Daily -At "23:45"
Register-ScheduledTask -TaskName "GMCapital_ConsultReport" -Action $a12 -Trigger $t12 -Settings $s10 -Force
Write-Host "  [OK] Consult Report - daily 23:45" -ForegroundColor Green

# 13. 대시보드 빌드 - daily 00:15 (다음날 새벽, 채점 후)
$a13 = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\build_dashboard.py`"" -WorkingDirectory $dir
$t13 = New-ScheduledTaskTrigger -Daily -At "00:15"
Register-ScheduledTask -TaskName "GMCapital_BuildDashboard" -Action $a13 -Trigger $t13 -Settings $s10 -Force
Write-Host "  [OK] Build Dashboard - daily 00:15" -ForegroundColor Green

Write-Host ""
Write-Host "GM Capital Automation Setup Complete!" -ForegroundColor Cyan
Write-Host "  Daily  : 04:00 / 06:30 / 07:30 / 18:00 / 22:30 / 23:00~06:00(hourly)" -ForegroundColor White
Write-Host "  ROY v2.4: 23:30 결과추적 / 23:45 야간컨설팅 / 00:15 대시보드" -ForegroundColor Cyan
Write-Host "  Weekly : Monday 07:00" -ForegroundColor White
Write-Host "  Biweekly: Sunday 08:00" -ForegroundColor White
Write-Host "  Logon  : Investment Journal watchdog" -ForegroundColor White
