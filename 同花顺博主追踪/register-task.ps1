# 注册「同花顺发文追踪-全能的野人」计划任务
# 触发规则：
#   1) 周一至周五 09:00 起，每 5 分钟一次，持续 6 小时 30 分（至 15:30）
#   2) 周一至周五 15:30 起，每 30 分钟一次，持续 8 小时 30 分（至 24:00）
#   3) 每天 00:00 起，每 30 分钟一次，持续 9 小时（至 09:00）

$ErrorActionPreference = 'Stop'

$TaskName = '同花顺发文追踪-全能的野人'
$VbsPath  = 'D:\情绪集合\同花顺博主追踪\run-hidden.vbs'

$Action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"' + $VbsPath + '"')

$Rep5  = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
          -RepetitionInterval (New-TimeSpan -Minutes 5) `
          -RepetitionDuration (New-TimeSpan -Hours 6 -Minutes 30)
$Rep30 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
          -RepetitionInterval (New-TimeSpan -Minutes 30) `
          -RepetitionDuration (New-TimeSpan -Hours 8 -Minutes 30)
$Rep30Night = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
          -RepetitionInterval (New-TimeSpan -Minutes 30) `
          -RepetitionDuration (New-TimeSpan -Hours 9)

$T1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '09:00'
$T1.Repetition = $Rep5.Repetition

$T2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '15:30'
$T2.Repetition = $Rep30.Repetition

$T3 = New-ScheduledTaskTrigger -Daily -At '00:00'
$T3.Repetition = $Rep30Night.Repetition

$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

$Principal = New-ScheduledTaskPrincipal -UserId ("$env:USERDOMAIN\$env:USERNAME") -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger @($T1, $T2, $T3) `
    -Settings $Settings -Principal $Principal `
    -Description '每 5 分钟（交易时段）/ 30 分钟（其余时段）抓取同花顺博主【全能的野人】新发文，写入 D:\情绪集合\【全能的野人】同花顺发文.md' `
    -Force | Out-Null

Write-Output '任务注册完成'
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State | Format-List
(Get-ScheduledTask -TaskName $TaskName).Triggers | ForEach-Object {
    "触发: $($_.StartBoundary) | 间隔: $($_.Repetition.Interval) | 持续: $($_.Repetition.Duration)"
}
