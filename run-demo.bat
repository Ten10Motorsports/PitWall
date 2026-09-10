@echo off
REM Runs a synthetic 24-car race so you can build your OBS scenes,
REM check every overlay and rehearse a broadcast without iRacing open.
setlocal
cd /d "%~dp0"
set PY=
where py >nul 2>nul && set PY=py -3
if "%PY%"=="" ( where python >nul 2>nul && set PY=python )
if "%PY%"=="" ( echo Python is not installed. See run.bat. & pause & exit /b 1 )
%PY% -m server.main --demo --demo-roster %*
if errorlevel 1 pause
