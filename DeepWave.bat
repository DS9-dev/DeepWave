@echo off
rem Запуск DeepWave двойным щелчком. Если программа ещё не собрана — запускается установка.
cd /d "%~dp0"
if exist "app\release\win-unpacked\DeepWave.exe" (
  start "" "app\release\win-unpacked\DeepWave.exe"
) else (
  call setup_app.bat
)
