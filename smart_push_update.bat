@echo off
:: Enable delayed expansion for dynamic variable handling in loops
setlocal enabledelayedexpansion
cls

:MAIN_MENU
echo ======================================================
echo SMVS APPROVAL SYSTEM - SMART UPDATE
echo ======================================================

echo LAST ACTIVITY: !LAST_PUSH!
echo 1. FULL UPDATE (Push everything)
echo 2. SELECTIVE UPDATE (Choose specific files)
echo 3. CANCEL
echo ======================================================
set /p mode="Choose option (1-3): "

if "%mode%"=="3" exit /b
if "%mode%" NEQ "1" if "%mode%" NEQ "2" exit /b

cls
echo ======================================================
echo CURRENT REPOSITORY STATUS & RECENT HISTORY
echo ======================================================
:: Display the target GitHub repository URL
git remote -v
echo.
:: Show the last 3 records with specific Date and Time formatting
echo RECENT UPDATED RECORDS:
git log -n 3 --pretty=format:"%%C(yellow)%%h %%C(reset)%%C(green)%%ad %%C(reset)%%s" --date=format:"%%d-%%m-%%Y %%H:%%M"
echo.
echo ======================================================
echo.

if "%mode%"=="1" goto FULL_UPDATE
if "%mode%"=="2" goto SELECTIVE_UPDATE

:FULL_UPDATE
echo [MODE: FULL UPDATE]
echo.
echo THE FOLLOWING FILES WILL BE UPDATED:
echo ------------------------------------------------------
:: List modified and new files
git status -s
echo ------------------------------------------------------
echo.

set /p confirm="Push ALL changes listed above to GitHub? (Y/N): "
if /i "%confirm%"=="Y" (
    git add .
    set msg=Full Update on %date% %time%
    git commit -m "!msg!"
    git push origin main
    echo.
    echo === Full Update Successfully Completed ===
) else (
    echo Update cancelled by user.
)
goto END

:SELECTIVE_UPDATE
echo [MODE: SELECTIVE UPDATE]
echo Opening file selection window...
set "selected_files="

:: Open a GUI window (PowerShell GridView) for checkbox-style file selection
set "psCommand=git status -s | Out-GridView -Title 'Select files to Push' -PassThru | ForEach-Object { $_.Substring(3) }"

for /f "delims=" %%i in ('powershell -command "%psCommand%"') do (
    set "selected_files=!selected_files! "%%i""
)

if "!selected_files!"=="" (
    echo.
    echo [CANCELLED] No files were selected in the window.
    pause
    exit /b
)

echo.
echo YOU SELECTED: !selected_files!
set /p confirm="Update ONLY these selected files? (Y/N): "

if /i "%confirm%"=="Y" (
    echo === Processing Selected Files... ===
    git add !selected_files!
    set msg=Selected Update: !selected_files! on %date% %time%
    git commit -m "!msg!"
    git push origin main
    echo.
    echo === Update Successfully Completed ===
) else (
    echo Update cancelled by user.
)
goto END

:END
pause