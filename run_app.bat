@echo off
cd /d "%~dp0"
".venv\Scripts\streamlit.exe" run "%~dp0app.py" --server.port 8502
