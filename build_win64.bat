@echo off
set PYTHONUNBUFFERED=1
poetry install --sync --no-root
poetry run py -3 -u qt6_build.py %*
