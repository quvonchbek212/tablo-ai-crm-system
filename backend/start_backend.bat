@echo off
cd /d "%~dp0"
echo TabloAI backend: http://127.0.0.1:8015
set "BACKEND_PYTHON=%~1"
if not defined BACKEND_PYTHON set "BACKEND_PYTHON=python"
"%BACKEND_PYTHON%" -m uvicorn app.main:app --reload --reload-dir app --host 127.0.0.1 --port 8015
echo.
echo Backend to'xtadi. Yuqoridagi xato matnini tekshiring.
pause
