@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Bataille navale - serveur
where py >nul 2>nul
if %errorlevel%==0 (
    py bataille_navale.py
) else (
    python bataille_navale.py
)
pause
