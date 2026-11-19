@echo off
cd /d "%~dp0"
where python >nul 2>nul && (python server.py & goto :end)
where py >nul 2>nul && (py server.py & goto :end)
echo Python is not installed. Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
:end
pause
