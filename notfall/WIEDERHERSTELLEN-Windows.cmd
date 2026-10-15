@echo off
rem Simple NAS - Notfall-Wiederherstellung (Windows). Doppelklick genuegt.
rem -ExecutionPolicy Bypass: das Skript ist nicht signiert und soll trotzdem laufen.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0programme\wiederherstellen.ps1"
echo.
pause
