@echo off
rem Drag a Prompt Graph JSON file onto this file to compile it to Python.
rem Output goes to a "<graph name>_out" folder next to the graph.
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  echo Drag a graph .json file onto compile.bat, or run:  compile.bat path\to\graph.json [character.json/png]
  pause
  exit /b 1
)
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
if "%~2"=="" (
  %PY% graphc.py compile "%~1" --target python
) else (
  %PY% graphc.py compile "%~1" --target python --character "%~2"
)
if errorlevel 1 (
  echo.
  echo Compile failed. See the message above.
) else (
  echo.
  echo Done. Open the _out folder and run: python chat_cli.py
)
pause
