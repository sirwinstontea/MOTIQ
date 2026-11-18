@echo off
rem MOTIQ without the installer: double-click this file in a downloaded or cloned MOTIQ folder.
rem First run opens the setup wizard; after that it starts MOTIQ. Extra arguments: --setup, --stop.
cd /d "%~dp0"
where py >nul 2>nul && (py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && (py -3 launcher\motiq.py %* & goto :eof))
where python >nul 2>nul && (python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && (python launcher\motiq.py %* & goto :eof))
where uv >nul 2>nul && (uv run --no-project --python 3.12 launcher\motiq.py %* & goto :eof)
echo MOTIQ needs Python 3.10 or newer to start its setup.
echo Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run MOTIQ.bat again.
echo Or use the MOTIQ installer (MOTIQ-Setup.exe), which needs nothing installed.
pause
