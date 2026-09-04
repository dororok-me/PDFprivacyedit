chcp 65001 >nul
@echo off
cd /d "%~dp0"
pip install -r requirements.txt -q
python app_web.py
pause
