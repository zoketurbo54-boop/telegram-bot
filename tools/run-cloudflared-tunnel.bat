@echo off
chcp 65001 >nul
title Cloudflare Quick Tunnel -^> localhost:8080
echo Сначала запусти Flask:  .venv\Scripts\python.exe miniapp\app.py
echo.

set "EXE=C:\Program Files\Cloudflare\cloudflared\cloudflared.exe"
if exist "%EXE%" goto run
set "EXE=C:\Program Files (x86)\Cloudflare\cloudflared\cloudflared.exe"
if exist "%EXE%" goto run

where cloudflared >nul 2>&1
if errorlevel 1 (
  echo cloudflared не найден. Установи: winget install Cloudflare.cloudflared
  pause
  exit /b 1
)
set "EXE=cloudflared"

:run
set "CAP=%~dp0cacert.pem"
if not exist "%CAP%" (
  echo Нет %CAP% — скачай https://curl.se/ca/cacert.pem в папку tools
  pause
  exit /b 1
)

echo Используется: %EXE%
echo Протокол http2 + CA bundle — на Windows cloudflared часто нужен --origin-ca-pool
echo Скопируй из вывода URL https://....trycloudflare.com в .env как WEBAPP_URL
echo.
"%EXE%" tunnel --url http://127.0.0.1:8080 --protocol http2 --edge-ip-version 4 --origin-ca-pool "%CAP%"
pause
