@echo off
REM ---------------------------------------------------------------
REM  PitWall - start the server
REM  Double-click this. That is the whole install.
REM ---------------------------------------------------------------
setlocal
cd /d "%~dp0"

set PY=
where py >nul 2>nul && set PY=py -3
if "%PY%"=="" ( where python >nul 2>nul && set PY=python )

if "%PY%"=="" (
  echo.
  echo   Python is not installed.
  echo.
  echo   Get it from https://www.python.org/downloads/  ^(any version 3.9 or newer^)
  echo   During install, tick "Add python.exe to PATH".
  echo.
  echo   PitWall needs nothing else. No pip install, no build tools.
  echo.
  pause
  exit /b 1
)

echo Starting PitWall...
%PY% -m server.main %*
if errorlevel 1 pause
