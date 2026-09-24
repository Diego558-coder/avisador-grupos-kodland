' Lanza el avisador en segundo plano, sin ventana visible,
' y guarda todo lo que va pasando en log.txt
Set objShell = CreateObject("WScript.Shell")
carpeta = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
objShell.CurrentDirectory = carpeta
comando = "cmd /c python avisador.py >> log.txt 2>&1"
objShell.Run comando, 0, False
