@echo off
cd /d "%~dp0"
echo ==================================================================
echo  Calibrazione camera sul braccio SENZA toccare il foglio
echo   1) hand-eye: il braccio guarda il foglio da 12-15 pose diverse
echo      -> scrive da sola data\calibration\markers_mm.json
echo   2) posa fissa: dalla posa di osservazione -> fixed_pose.json
echo  Il foglio ArUco resta FERMO sul tavolo dall'inizio alla fine.
echo  Chiudete prima run_server.bat, run_demo.bat e RealSense Viewer.
echo ==================================================================
echo.
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [ERRORE] .venv non trovato: esegui prima setup.bat
    pause
    exit /b 1
)
echo Da dove leggo la posa del braccio? (sola lettura, non lo muove mai)
echo   1 = direttamente dall'UR via rete (RTDE, serve l'IP del robot)
echo   2 = RoboDK collegato al robot vero
echo   3 = la scrivo io dal pendant a ogni posa (x y z in mm, rx ry rz in rad)
set "SRC=1"
set /p "SRC=Scelta [1]: "
set "TCPARGS=--tcp rtde"
if "%SRC%"=="2" set "TCPARGS=--tcp robodk"
if "%SRC%"=="3" set "TCPARGS=--tcp manual"
if "%SRC%"=="1" (
    "%PY%" -c "import rtde_receive" 2>nul || (
        echo Installo ur_rtde ^(solo lettura della posa^)...
        "%PY%" -m pip install ur_rtde || (echo [ERRORE] installazione ur_rtde fallita & pause & exit /b 1)
    )
    set "IP="
    set /p "IP=IP del robot UR: "
)
if "%SRC%"=="1" set "TCPARGS=--tcp rtde --robot-ip %IP%"
if "%SRC%"=="2" set "PYTHONPATH=C:\RoboDK\Python"
echo.
echo PASSO 1 - Si apre la finestra della camera. Per ogni posa (12-15):
echo   muovete il braccio dal pendant con il foglio inquadrato e RUOTATO in modo diverso,
echo   fermatelo, poi SPAZIO. Alla fine C per calcolare (Q per uscire).
"%PY%" -m vision.eyes.cli calibrate-hand-eye %TCPARGS% || (pause & exit /b 1)
echo.
echo PASSO 2 - Portate il braccio nella POSA DI OSSERVAZIONE (il foglio non si tocca).
pause
"%PY%" -m vision.eyes.cli calibrate-look-pose
echo.
echo Fatto. Ora togliete il foglio, avviate run_server.bat e registrate il tavolo vuoto:
echo   curl.exe -X POST http://localhost:8000/empty_table/arm
pause
