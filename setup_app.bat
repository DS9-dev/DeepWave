@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo.
echo  ===  DeepWave: установка и сборка  ===
echo  Каталог, история, настройки и вход в Яндекс сохраняются — пересобирается только программа.
echo.

tasklist /FI "IMAGENAME eq DeepWave.exe" 2>nul | find /I "DeepWave.exe" >nul
if not errorlevel 1 goto running
tasklist /FI "IMAGENAME eq Music App.exe" 2>nul | find /I "Music App.exe" >nul
if not errorlevel 1 goto running
goto checks
:running
echo  [!] DeepWave сейчас запущен. Закройте его и запустите установку снова.
pause
exit /b 1

:checks
where node >nul 2>nul
if errorlevel 1 (
  echo  [!] Не найден Node.js.
  echo      Установите Node.js LTS: https://nodejs.org/  или командой:  winget install OpenJS.NodeJS.LTS
  echo      Затем закройте это окно и запустите setup_app.bat ещё раз.
  pause
  exit /b 1
)
for /f "delims=" %%v in ('node -v') do echo  Node.js %%v

python -c "import yandex_music, requests" >nul 2>nul
if errorlevel 1 (
  echo  Устанавливаю библиотеку yandex-music для Python...
  python -m pip install -U yandex-music
  if errorlevel 1 (
    echo  [!] Не удалось установить yandex-music. Проверьте, что Python установлен и доступен как "python".
    pause
    exit /b 1
  )
)
for /f "delims=" %%v in ('python -c "import sys;print(sys.version.split()[0])"') do echo  Python %%v

cd app
echo.
echo  Загружаю компоненты (только при первой установке или обновлении, ~150 МБ)...
call npm ci
if errorlevel 1 (
  echo  npm ci не удался, пробую npm install...
  call npm install
  if errorlevel 1 (
    echo  [!] Ошибка установки компонентов.
    pause
    exit /b 1
  )
)
echo.
echo  Собираю DeepWave...
if exist "release\win-unpacked\Music App.exe" del /q "release\win-unpacked\Music App.exe"
call npm run dist
if errorlevel 1 (
  echo  [!] Ошибка сборки.
  pause
  exit /b 1
)
cd ..

set "EXE=%~dp0app\release\win-unpacked\DeepWave.exe"
if not exist "%EXE%" (
  echo  [!] Не найден "%EXE%".
  pause
  exit /b 1
)
echo.
echo  Создаю ярлыки (рабочий стол и меню «Пуск»)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$w=New-Object -ComObject WScript.Shell; foreach($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) { $s=$w.CreateShortcut((Join-Path $d 'DeepWave.lnk')); $s.TargetPath=$env:EXE; $s.WorkingDirectory=(Split-Path $env:EXE); $s.IconLocation=$env:EXE+',0'; $s.Description='DeepWave - unofficial Yandex Music client'; $s.Save() }; $old=Join-Path ([Environment]::GetFolderPath('Desktop')) 'Music App.lnk'; if(Test-Path $old){Remove-Item $old}"
echo.
echo  Готово! DeepWave можно запускать ярлыком на рабочем столе, из меню «Пуск» или файлом DeepWave.bat.
echo  Запускаю...
start "" "%EXE%"
timeout /t 3 >nul
endlocal
