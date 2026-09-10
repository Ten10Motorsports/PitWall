@echo off
REM ===========================================================================
REM  PitWall - push this folder to GitHub, correctly.
REM
REM  Double-click this instead of dragging files into the GitHub website.
REM
REM  Dragging files into a browser flattens every folder and silently skips
REM  the .github folder, because Windows hides names starting with a dot.
REM  The result is eighty loose files, several of them renamed "index (1).html"
REM  and friends, and none of the automation. This script pushes the whole
REM  folder exactly as it is - folders, hidden files and all.
REM ===========================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo   PitWall - upload to GitHub
echo   ============================================================
echo.

REM --- 1. Is Git installed? --------------------------------------------------
where git >nul 2>nul
if errorlevel 1 (
  echo   Git is not installed, and this script needs it.
  echo.
  echo   Install it one of these ways, then run this file again:
  echo.
  echo     * Easiest:  open a Command Prompt and run
  echo                   winget install --id Git.Git -e
  echo.
  echo     * Or download it from  https://git-scm.com/download/win
  echo       ^(accept every default during the installer^)
  echo.
  echo   If you would rather not install anything, use GitHub Desktop
  echo   instead - https://desktop.github.com - or follow the
  echo   "Fix the repository upload" page.
  echo.
  pause
  exit /b 1
)

REM --- 2. Sanity check: are we in the right folder? --------------------------
if not exist "server\main.py" (
  echo   This does not look like the PitWall folder.
  echo.
  echo   Expected to find server\main.py next to this script.
  echo   Move setup-github.bat into the extracted PitWall folder and
  echo   run it from there.
  echo.
  pause
  exit /b 1
)
if not exist ".github\workflows" (
  echo   Warning: the .github folder is missing from THIS folder.
  echo   Re-extract the PitWall zip before continuing, or the automation
  echo   will not be uploaded.
  echo.
  set /p CONT="   Continue anyway? (y/N): "
  if /i not "!CONT!"=="y" exit /b 1
)

REM --- 3. Where is it going? -------------------------------------------------
set "REPOURL=%~1"
if "%REPOURL%"=="" (
  echo   Paste the address of your EMPTY GitHub repository.
  echo   It looks like:  https://github.com/YourOrg/YourRepo.git
  echo.
  set /p REPOURL="   Repository URL: "
)
if "!REPOURL!"=="" (
  echo.
  echo   No URL given. Nothing has been changed.
  pause
  exit /b 1
)
echo !REPOURL! | find /i "github.com" >nul
if errorlevel 1 (
  echo.
  echo   That does not look like a GitHub URL. Nothing has been changed.
  pause
  exit /b 1
)

echo.
echo   Uploading everything in this folder to:
echo     !REPOURL!
echo.
echo   Anything already in that repository will be REPLACED.
set /p GO="   Continue? (y/N): "
if /i not "!GO!"=="y" (
  echo   Cancelled. Nothing has been changed.
  pause
  exit /b 0
)

REM --- 4. Identity, if this machine has none --------------------------------
git config user.email >nul 2>nul
if errorlevel 1 (
  git config --global user.email "you@example.com"
  git config --global user.name "PitWall Setup"
  echo   [i] Set a placeholder Git name and email for this machine.
)

REM --- 5. Do it --------------------------------------------------------------
echo.
if not exist ".git" (
  echo   [1/5] Preparing the folder...
  git init -q
) else (
  echo   [1/5] Folder is already prepared.
)

echo   [2/5] Collecting every file, including hidden ones...
git add -A
if errorlevel 1 goto :failed

echo   [3/5] Saving a snapshot...
git commit -q -m "PitWall" 2>nul
if errorlevel 1 echo         ^(nothing new to save - continuing^)

echo   [4/5] Pointing at your repository...
git remote remove origin >nul 2>nul
git remote add origin "!REPOURL!"
if errorlevel 1 goto :failed
git branch -M main

echo   [5/5] Uploading...
git push -u origin main --force
if errorlevel 1 goto :pushfailed

echo.
echo   ============================================================
echo   Done. Everything is on GitHub with its folders intact.
echo.
echo   Next, on the GitHub website:
echo.
echo     1. Settings ^> Actions ^> General ^> Workflow permissions
echo        Choose "Read and write permissions" and Save.
echo.
echo     2. Settings ^> Pages ^> Source
echo        Choose "GitHub Actions". Ignore the Configure buttons.
echo.
echo     3. Actions tab ^> "Publish site"      ^> Run workflow
echo        Actions tab ^> "Build Windows app" ^> Run workflow
echo.
echo   Check the Code tab first: you should see folders such as
echo   server, web and .github - not a wall of loose files.
echo   ============================================================
echo.
pause
exit /b 0

:pushfailed
echo.
echo   The upload was refused.
echo.
echo   Most likely one of these:
echo.
echo     * A sign-in window appeared and was closed or cancelled.
echo       Run this file again and complete the GitHub sign-in.
echo.
echo     * The repository URL is wrong, or the repository does not
echo       exist yet. Create it on GitHub first, empty, with no
echo       README ticked.
echo.
echo     * Your account does not have write access to it.
echo.
pause
exit /b 1

:failed
echo.
echo   Something went wrong above. Nothing was uploaded.
echo   The message printed by Git just above this line says why.
echo.
pause
exit /b 1
