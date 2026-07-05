@echo off
"%~dp0..\.venv\Scripts\python.exe" "%~dp0..\scripts\html2epub.py" %*
exit /b %errorlevel%
