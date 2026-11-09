@echo off
title Kultiva PO AI Agent Web App
echo Starting Purchase Order to Sales Order AI Agent Web App...
echo.
cd /d "%~dp0"
"C:\Users\kevin\AppData\Local\Python\pythoncore-3.14-64\python.exe" -m streamlit run app.py
pause

