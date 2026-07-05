@echo off
"%~dp0..\.venv\Scripts\python.exe" "%~dp0..\scripts\pdf2xhtml.py" %*
exit /b %errorlevel%
