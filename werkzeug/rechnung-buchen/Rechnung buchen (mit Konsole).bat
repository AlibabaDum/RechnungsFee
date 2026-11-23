@echo off
rem  Rechnungen buchen - Start MIT Konsolenfenster (zum Nachsehen).
rem  Fuer den normalen Start ohne schwarzes Fenster: "Rechnung buchen.vbs".
rem
rem  Diese Datei gehoert zum Ordner: die noetige Python liegt im Unterordner
rem  "python", das Programm im Unterordner "belegerkennung". Man kann den
rem  ganzen Ordner beliebig verschieben oder auf einen USB-Stick kopieren.
rem  Eine PDF-Datei kann auch auf diese Datei gezogen werden - dann wird sie
rem  direkt gelesen und die Liste zeigt die erkannten Rechnungen.
set "WURZEL=%~dp0"
set "PY=%WURZEL%python\python.exe"
if not exist "%PY%" (
  echo Die mitgebrachte Python fehlt in diesem Ordner:
  echo   %PY%
  echo Bitte den kompletten Ordner inklusive Unterordner "python" kopieren.
  pause
  exit /b 2
)
cd /d "%WURZEL%"
if "%~1"=="" (
  "%PY%" "%WURZEL%buchen.py"
) else (
  "%PY%" "%WURZEL%buchen.py" "%~1" --gui
)
if errorlevel 1 (
  echo.
  echo Etwas ist schiefgelaufen. Steht oben eine Fehlermeldung?
  pause
)
