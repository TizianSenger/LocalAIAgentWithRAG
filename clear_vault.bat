@echo off
echo.
echo This will delete all indexed notes in the Obsidian vault:
echo   C:\natMSSObsidian\natMSS\Code
echo   C:\natMSSObsidian\natMSS\.indexer_state.json
echo.
set /p CONFIRM=Are you sure? (y/n): 
if /i not "%CONFIRM%"=="y" (
    echo Aborted.
    exit /b 0
)

echo.
echo Clearing vault...

if exist "C:\natMSSObsidian\natMSS\Code" (
    rmdir /s /q "C:\natMSSObsidian\natMSS\Code"
    echo   [deleted] Code folder
) else (
    echo   [skip] Code folder not found
)

if exist "C:\natMSSObsidian\natMSS\.indexer_state.json" (
    del /f /q "C:\natMSSObsidian\natMSS\.indexer_state.json"
    echo   [deleted] .indexer_state.json
) else (
    echo   [skip] .indexer_state.json not found
)

echo.
echo Done. Run "python indexer.py" to re-index from scratch.
pause
