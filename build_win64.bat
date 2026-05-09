@echo off
setlocal
set PYTHONUNBUFFERED=1

where mise >nul 2>nul
if errorlevel 1 (
	echo Error: mise is required but was not found in PATH. 1>&2
	exit /b 1
)

mise install
if errorlevel 1 exit /b %errorlevel%
mise exec -- poetry install --sync --no-root
if errorlevel 1 exit /b %errorlevel%
mise exec -- poetry run python -u .\qt6_build.py %*
exit /b %errorlevel%
