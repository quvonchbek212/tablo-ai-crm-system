@echo off
echo ========================================================
echo  TabloAI - AI data yordamchi (FastAPI + React + OpenAI)
echo ========================================================
echo.

set "PYTHON_EXE=%~dp0venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" goto system_python
"%PYTHON_EXE%" -c "import uvicorn,fastapi,sqlalchemy,httpx,sqlglot,sshtunnel,pydantic_settings,multipart,openpyxl; from websockets.asyncio.client import connect" >nul 2>&1
if errorlevel 1 goto system_python
goto python_ready

:system_python
set "PYTHON_EXE=python"
python -c "import uvicorn,fastapi,sqlalchemy,httpx,sqlglot,sshtunnel,pydantic_settings,multipart,openpyxl; from websockets.asyncio.client import connect" >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python muhitida backend kutubxonalari yetishmaydi.
  echo Avval backend kutubxonalarini o'rnating: python -m pip install -r backend\requirements.txt
  pause
  exit /b 1
)

:python_ready
where npm.cmd >nul 2>&1
if errorlevel 1 (
  echo ERROR: npm.cmd topilmadi. Node.js o'rnatilganini tekshiring.
  pause
  exit /b 1
)

netstat -ano -p tcp | findstr /R /C:":8015 .*LISTENING" >nul
if not errorlevel 1 (
  echo ERROR: 8015 port band. Eski backend oynasini yoping va qayta urinib ko'ring.
  pause
  exit /b 1
)

netstat -ano -p tcp | findstr /R /C:":5173 .*LISTENING" >nul
if not errorlevel 1 (
  echo ERROR: 5173 port band. Eski frontend oynasini yoping va qayta urinib ko'ring.
  pause
  exit /b 1
)

echo Starting FastAPI Backend on http://localhost:8015...
start "TabloAI Backend" "%~dp0backend\start_backend.bat" "%PYTHON_EXE%"

echo Starting React Frontend on http://localhost:5173...
start "TabloAI Frontend" "%~dp0frontend\start_frontend.bat"

echo.
echo Backend va frontend jarayonlari ishga tushirish uchun yuborildi.
echo Namuna baza birinchi ishga tushishda avtomatik yaratiladi.
echo OpenAI uchun backend\.env ichida OPENAI_API_KEY ni sozlang.
echo Brauzerda oching: http://localhost:5173
echo Backend API Docs: http://localhost:8015/docs
echo ========================================================
exit /b 0
