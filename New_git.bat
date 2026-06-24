@echo off
SETLOCAL Enabledelayedexpansion
cls

:MAIN_MENU
echo ====================================================
echo  SMVS APPROVAL - DYNAMIC FRESH GITHUB UPLOAD
echo ====================================================

:: 1. FETCH LAST FULL COMMIT (Searching for "Full Update" in history)
for /f "delims=" %%i in ('git log --grep="Full Update" -1 --pretty^=format:"%%C(green)%%ad %%C(yellow)[%%h] %%C(reset)%%s" --date^=format:"%%d-%%m-%%Y %%H:%%M" 2^>nul') do (
    set "LAST_FULL=%%i"
)

:: 2. FETCH LAST 2 MODIFIED/SELECTED COMMITS (Searching for "Selected Update")
set "count=0"
echo LAST FULL UPLOAD: !LAST_FULL!
echo.
echo RECENT MODIFIED UPDATES:
for /f "delims=" %%i in ('git log --grep="Selected Update" -2 --pretty^=format:"%%C(green)%%ad %%C(yellow)[%%h] %%C(reset)%%s" --date^=format:"%%d-%%m-%%Y %%H:%%M" 2^>nul') do (
    echo %%i
    set /a count+=1
)
if !count! equ 0 echo [No recent modified updates found]

echo ====================================================

:: STEP 1: CAPTURE USER & REPO DETAILS
echo.
set /p "GH_USER=Enter GitHub Username: "
set /p "GH_REPO=Enter Repository Name: "
set /p "GH_BRANCH=Enter Branch (e.g. main): "
echo.

:: Construct the URL and verify
SET "REPO_URL=https://github.com/!GH_USER!/!GH_REPO!.git"
echo [INFO] Target URL: %REPO_URL%
echo.

:: STEP 2: CLEAN PREVIOUS GIT HISTORY
if exist ".git" (
    echo [INFO] Removing existing .git directory...
    rmdir /s /q ".git" 2>nul
    if exist ".git" (
        echo [ERROR] Failed to delete .git. Close VS Code and try again.
        pause
        exit /b
    )
)

:: STEP 3: INITIALIZE & CONFIGURE IDENTITY
echo [INFO] Initializing fresh repository...
git init
git config user.name "!GH_USER!"
echo [SUCCESS] Configured local Git user: !GH_USER!

:: STEP 4: AUTOMATED STAGING & DYNAMIC COMMIT
echo [INFO] Staging all project files...
git add .
set "msg=Full Update on %date% %time%"
git commit -m "!msg!"
echo [SUCCESS] Committed with message: !msg!

:: STEP 5: CONNECT & FORCE PUSH
echo.
echo [INFO] Connecting to remote origin...
git remote add origin %REPO_URL%
git branch -M !GH_BRANCH!

echo [INFO] Force-pushing to GitHub...
git push -u origin !GH_BRANCH! -f

if %errorlevel% eq 0 (
    echo.
    echo ====================================================
    echo  [SUCCESS] Code successfully pushed to !GH_REPO!
    echo ====================================================
) else (
    echo.
    echo ====================================================
    echo  [ERROR] Push failed. Verify repository name.
    echo ====================================================
)

pause