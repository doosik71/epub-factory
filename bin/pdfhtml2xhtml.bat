@echo off
"%~dp0..\.venv\Scripts\python.exe" "%~dp0..\scripts\pdfhtml2xhtml.py" %*
exit /b %errorlevel%
