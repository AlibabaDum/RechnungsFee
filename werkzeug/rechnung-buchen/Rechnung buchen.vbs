'  Rechnungen buchen - Start ohne schwarzes Konsolenfenster.
'
'  Doppeltes Klicken:  die Liste der Buchungssaetze offnet sich.
'  PDF darauf ziehen:  diese PDF wird sofort eingelesen und angezeigt.
'
'  Diese Datei startet die mitgebrachte Python im Unterordner "python" in ihrer
'  Fensterlos-Version (pythonw.exe). Meldungen erscheinen deshalb als Fenster,
'  nicht in einer Konsole. Die Datei "Rechnung buchen (mit Konsole).bat" im
'  selben Ordner zeigt zusaetzlich alles im schwarzen Fenster an - nur zum
'  Nachsehen, wenn etwas nicht klappt.
Option Explicit

Dim fso, schale, ordner, python, programm, befehl, i
Set fso = CreateObject("Scripting.FileSystemObject")
Set schale = CreateObject("WScript.Shell")

ordner = fso.GetParentFolderName(WScript.ScriptFullName)
programm = ordner & "\buchen.py"
python = ordner & "\python\pythonw.exe"

If Not fso.FileExists(programm) Then
  MsgBox "Das Programm gehoert in denselben Ordner wie diese Datei." & vbCrLf & _
         "Es fehlt: " & programm, vbExclamation, "Rechnungen buchen"
  WScript.Quit 2
End If

If Not fso.FileExists(python) Then
  MsgBox "Die mitgebrachte Python fehlt in diesem Ordner:" & vbCrLf & python & _
         vbCrLf & vbCrLf & "Bitte den kompletten Ordner inklusive Unterordner " & _
         """python"" kopieren.", vbExclamation, "Rechnungen buchen"
  WScript.Quit 2
End If

'  Programmpfad und - falls ein PDF gezogen wurde - die PDF-Datei anhaengen.
befehl = Chr(34) & python & Chr(34) & " " & Chr(34) & programm & Chr(34)
For i = 0 To WScript.Arguments.Count - 1
  befehl = befehl & " " & Chr(34) & WScript.Arguments(i) & Chr(34)
Next
If WScript.Arguments.Count > 0 Then befehl = befehl & " --gui"

schale.CurrentDirectory = ordner
On Error Resume Next
'  1 = normales Fenster: die Fensterlos-Version der Python (pythonw.exe) bringt
'  von sich aus keine Konsole mit, die Liste ist ein eigenes Fenster.
schale.Run befehl, 1, False
If Err.Number <> 0 Then
  MsgBox "Der Start hat nicht geklappt: " & Err.Description & vbCrLf & vbCrLf & _
         "Aufgerufen wurde: " & befehl, vbExclamation, "Rechnungen buchen"
  WScript.Quit 1
End If
