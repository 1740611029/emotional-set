' 静默启动【全能的野人】同花顺发文采集脚本（0 = 不显示窗口）
' 路径全部动态推导：项目路径基于脚本自身位置；node 从 PATH / NVM / 默认安装位置自动探测。
' 找到合适的 Node 后写入 logs\node-path.txt，后续直接复用，不再反复探测。
' 换电脑 / 换 Node 版本后缓存失效会自动重新探测，全程无黑窗。
' 克隆仓库到任意电脑后，运行仓库根目录的「初始化-2-注册同花顺发文追踪任务.ps1」注册计划任务即可。
' 运行环境要求：Node.js 21+（collect.js 使用全局 WebSocket 连接 Chrome CDP）。
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\") - 1)
sh.CurrentDirectory = scriptDir

Const MIN_NODE_MAJOR = 21
cacheFile = scriptDir & "\logs\node-path.txt"

EnsureLogDir()

nodeExe = FindNode()
If nodeExe = "" Then
  fso.OpenTextFile(scriptDir & "\logs\run.log", 8, True).WriteLine "[" & Now & "] 未找到 Node.js 21+，本轮采集未执行（请安装 Node.js 21 或更高版本）"
  WScript.Quit 1
End If

sh.Run """" & nodeExe & """ """ & scriptDir & "\collect.js""", 0, False

' 优先读缓存；缓存不可用再按 PATH → NVM shim → 官网默认 → NVM installs 下最高版本探测。
' 探测成功后写回缓存，下次直接复用，不再做任何版本探测。
Function FindNode()
  Dim cached, m, p, installs, folder, subF, sp, best, bestMajor

  cached = ReadCache()
  If cached <> "" Then
    If LCase(cached) = "node" Then
      FindNode = "node" : Exit Function
    ElseIf fso.FileExists(cached) Then
      FindNode = cached : Exit Function
    End If
  End If

  ' 缓存缺失或失效：静默重新探测
  If NodeMajor("node") >= MIN_NODE_MAJOR Then
    WriteCache "node"
    FindNode = "node"
    Exit Function
  End If

  p = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Author Software\nvm\.nodejs\node.exe")
  If fso.FileExists(p) Then
    m = NodeMajor(p)
    If m >= MIN_NODE_MAJOR Then WriteCache p : FindNode = p : Exit Function
  End If

  p = sh.ExpandEnvironmentStrings("%ProgramFiles%\nodejs\node.exe")
  If fso.FileExists(p) Then
    m = NodeMajor(p)
    If m >= MIN_NODE_MAJOR Then WriteCache p : FindNode = p : Exit Function
  End If

  installs = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Author Software\nvm\installs")
  best = "" : bestMajor = 0
  If fso.FolderExists(installs) Then
    Set folder = fso.GetFolder(installs)
    For Each subF In folder.SubFolders
      sp = subF.Path & "\node.exe"
      If fso.FileExists(sp) Then
        m = NodeMajor(sp)
        If m >= MIN_NODE_MAJOR And m > bestMajor Then bestMajor = m : best = sp
      End If
    Next
  End If

  If best <> "" Then WriteCache best
  FindNode = best
End Function

' 用隐藏的 cmd 重定向读取版本号，不弹黑窗；文件不存在或读不到版本返回 0。
Function NodeMajor(exe)
  Dim tmp, cmd, ts, s, parts, js
  NodeMajor = 0
  On Error Resume Next
  tmp = sh.ExpandEnvironmentStrings("%TEMP%") & "\" & fso.GetTempName()
  js = "require('fs').writeFileSync(process.argv[1], process.versions.node)"
  cmd = """" & exe & """ -e """ & js & """ """ & tmp & """"
  sh.Run cmd, 0, True
  If fso.FileExists(tmp) Then
    Set ts = fso.OpenTextFile(tmp, 1, False)
    If Not ts.AtEndOfStream Then
      s = Trim(ts.ReadLine)
      parts = Split(s, ".")
      If IsNumeric(parts(0)) Then NodeMajor = CInt(parts(0))
    End If
    ts.Close
    fso.DeleteFile tmp, True
  End If
  On Error GoTo 0
End Function

Function ReadCache()
  Dim ts
  ReadCache = ""
  On Error Resume Next
  If fso.FileExists(cacheFile) Then
    Set ts = fso.OpenTextFile(cacheFile, 1, False)
    If Not ts.AtEndOfStream Then ReadCache = Trim(ts.ReadLine)
    ts.Close
  End If
  On Error GoTo 0
End Function

Sub WriteCache(ByVal nodePath)
  Dim ts
  On Error Resume Next
  Set ts = fso.OpenTextFile(cacheFile, 2, True)
  ts.WriteLine nodePath
  ts.Close
  On Error GoTo 0
End Sub

Sub EnsureLogDir()
  If Not fso.FolderExists(scriptDir & "\logs") Then fso.CreateFolder scriptDir & "\logs"
End Sub
