' Orac Voice launcher (no console window).
' Double-click to start; double-click again to open the settings page.
Set sh = CreateObject("WScript.Shell")
folder = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
sh.CurrentDirectory = folder
sh.Run "pythonw flow.py", 0, False
