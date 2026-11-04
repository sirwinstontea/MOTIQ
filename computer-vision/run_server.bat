@echo off
cd /d "%~dp0"
echo ========================================================
echo  Server Visione per l'app e Grok Voice (porta 8000)
echo  Camera: RealSense USB-C se c'e', se no la webcam del PC
echo  Non usare insieme a run_demo.bat: la camera e' una sola
echo ========================================================
echo.
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [ERRORE] .venv non trovato: esegui prima setup.bat
    pause
    exit /b 1
)
"%PY%" -m vision.eyes.cli run --port 8000 %*
pause
