@echo off
cd /d "%~dp0ui"

if not exist "node_modules" (
    echo Installing Electron, please wait...
    npm install
    echo.
)

echo Starting natMSS Agent UI...
npx electron .
