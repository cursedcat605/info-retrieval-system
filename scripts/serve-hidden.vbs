' Launch the IRS web server supervisor in the background (no console window).
' Double-click to run, or drop a shortcut into the Startup folder for auto-start.
Option Explicit

Dim fso, sh, root, ps1
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
ps1  = root & "\scripts\serve.ps1"

If Not fso.FileExists(ps1) Then
    MsgBox "serve.ps1 not found: " & ps1, 16, "IRS"
    WScript.Quit 1
End If

sh.CurrentDirectory = root
sh.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & ps1 & """", 0, False
