@echo off
rem Simple NAS - Notfall-Wiederherstellung (Windows). Doppelklick genuegt.

rem Nur Zutaten aus GitHub statt des fertigen Pakets? Dann fehlen restic/rclone.
if not exist "%~dp0programme\windows-amd64\restic.exe" (
  echo.
  echo  Hier fehlen die Programme restic und rclone.
  echo.
  echo  Die Dateien aus dem GitHub-Ordner "notfall" sind nur der Bauplan.
  echo  Gebraucht wird das fertige Notfall-Paket SimpleNAS-Notfall-DATUM.zip:
  echo  bauen mit   bash notfall/paket-bauen.sh   und das ZIP komplett auspacken.
  echo.
  pause
  exit /b 1
)

rem Aus dem Internet geladene Dateien (auch aus einem geladenen ZIP) tragen eine
rem Markierung. Gibt eine Gruppenrichtlinie "RemoteSigned" vor, verweigert
rem PowerShell dann das unsignierte Skript - auch mit -ExecutionPolicy Bypass,
rem denn die Richtlinie hat Vorrang. Unblock-File entfernt die Markierung.
powershell.exe -NoProfile -Command "Get-ChildItem -LiteralPath '%~dp0.' -Recurse -File | Unblock-File"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0programme\wiederherstellen.ps1"
echo.
pause
