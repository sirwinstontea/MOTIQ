@echo off
cd /d "%~dp0"
echo ========================================================
echo  Calibrazione camera sul braccio (posa fissa)
echo  Prima di partire:
echo   1. foglio ArUco sul tavolo, centri dei marker misurati
echo      col TCP (frame Base) in data\calibration\markers_mm.json
echo   2. braccio nella POSA DI OSSERVAZIONE
echo   3. chiusi run_server.bat, run_demo.bat e RealSense Viewer
echo ========================================================
echo.
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [ERRORE] .venv non trovato: esegui prima setup.bat
    pause
    exit /b 1
)
if not "%~1"=="" (
    "%PY%" -m vision.eyes.cli calibrate-look-pose %*
    pause
    exit /b
)
set "TABLEZ="
set /p "TABLEZ=Quota Z del tavolo nel frame Base, in mm (Invio = 0): "
if "%TABLEZ%"=="" set "TABLEZ=0"
"%PY%" -m vision.eyes.cli calibrate-look-pose --table-z-mm %TABLEZ%
echo.
echo Controllo: errore di riproiezione ^< 1 px, profondita' entro +/-5 mm, immagine in logs\look_pose_check.jpg
echo Per verificare piu' tardi senza salvare:  calibra_braccio.bat --check --table-z-mm %TABLEZ%
pause
