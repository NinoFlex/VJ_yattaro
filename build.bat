@echo off
call "%~dp0build.cmd" %*
exit /b %errorlevel%
