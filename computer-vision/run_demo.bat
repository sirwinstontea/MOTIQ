@echo off
cd /d "%~dp0"
echo ========================================================
echo  Avvio Demo Live: Visione A (Occhi) + Visione B (Grok)
echo ========================================================
echo.
rem Usa il Python del .venv (dove setup.bat installa i pacchetti), non quello di sistema
rem (.venv e' in .gitignore: in un clone nuovo va creato con setup.bat)
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [ERRORE] .venv non trovato: esegui prima setup.bat
    pause
    exit /b 1
)
"%PY%" demo_live.py %*
if errorlevel 1 (
    echo.
    echo [Info] Se non hai una webcam collegata, provo con la modalita replay:
    "%PY%" demo_live.py --replay %*
)
pause
