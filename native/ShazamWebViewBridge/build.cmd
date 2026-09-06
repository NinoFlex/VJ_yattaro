@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\..\tools\build-helper.ps1" %*
exit /b %errorlevel%
