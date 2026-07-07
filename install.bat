@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "INSTALLER=%SCRIPT_DIR%scripts\install_writeryang.py"

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%INSTALLER%" %*
  exit /b %ERRORLEVEL%
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python "%INSTALLER%" %*
  exit /b %ERRORLEVEL%
)

where python3 >nul 2>nul
if %ERRORLEVEL%==0 (
  python3 "%INSTALLER%" %*
  exit /b %ERRORLEVEL%
)

echo error: python is required to run the WriterYang installer 1>&2
exit /b 1
