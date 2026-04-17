@echo off
setlocal

:: ---------------------------------------------------------------
:: Settings — adjust WORKERS to match INDEXER_WORKERS in config.py
:: ---------------------------------------------------------------
set WORKERS=4
set PYTHON=python

:: ---------------------------------------------------------------
:: 1. Stop any running Ollama instance
:: ---------------------------------------------------------------
echo [1/4] Stopping existing Ollama instance (if any)...
taskkill /f /im ollama.exe >nul 2>&1
timeout /t 2 /nobreak >nul

:: ---------------------------------------------------------------
:: 2. Start Ollama with parallel support in a new window
:: ---------------------------------------------------------------
echo [2/4] Starting Ollama with %WORKERS% parallel slots...
set OLLAMA_NUM_PARALLEL=%WORKERS%
set OLLAMA_MAX_LOADED_MODELS=1
start "Ollama Server" /min cmd /c "set OLLAMA_NUM_PARALLEL=%WORKERS% && set OLLAMA_MAX_LOADED_MODELS=1 && ollama serve"

:: ---------------------------------------------------------------
:: 3. Wait until Ollama is ready (poll /api/tags)
:: ---------------------------------------------------------------
echo [3/4] Waiting for Ollama to be ready...
:wait_loop
timeout /t 2 /nobreak >nul
curl -s http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 goto wait_loop
echo        Ollama is ready.

:: ---------------------------------------------------------------
:: 4. Run the indexer
:: ---------------------------------------------------------------
echo [4/4] Starting indexer...
echo.
cd /d "%~dp0"
%PYTHON% indexer.py %*

echo.
echo Indexer finished.
pause
