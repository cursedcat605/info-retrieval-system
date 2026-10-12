' Launch the IRS Cloudflare tunnel supervisor in the background (no console window).
' Double-click to run. Stop it with: scripts\tunnel.ps1 -Stop
Option Explicit

Dim fso, sh, root, ps1
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
ps1  = root & "\scripts\tunnel.ps1"

If Not fso.FileExists(ps1) Then
    MsgBox "tunnel.ps1 not found: " & ps1, 16, "IRS"
    WScript.Quit 1
End If

sh.CurrentDirectory = root
sh.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & ps1 & """", 0, False
