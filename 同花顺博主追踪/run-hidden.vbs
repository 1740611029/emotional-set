' 静默启动【全能的野人】同花顺发文采集脚本（0 = 不显示窗口）
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "D:\情绪集合\同花顺博主追踪"
sh.Run """E:\nvm\nodejs\node.exe"" ""D:\情绪集合\同花顺博主追踪\collect.js""", 0, False
