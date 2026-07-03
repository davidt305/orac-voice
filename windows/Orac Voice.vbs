' Orac Voice: launcher sin ventana de consola.
' Doble click para iniciar; doble click de nuevo abre los ajustes en el navegador.
Set sh = CreateObject("WScript.Shell")
folder = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
sh.CurrentDirectory = folder
sh.Run "pythonw flow.py", 0, False
