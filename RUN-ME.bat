@echo off
REM ===========================================================================
REM  Collage Studio - one-press launcher (double-click me)
REM
REM  Fetches the files into C:\Zapps\collage-studio, checks Python and ffmpeg,
REM  then opens the board. Safe to re-run; it just refreshes the files.
REM ===========================================================================

setlocal

set "TARGET=%~dp0"

echo.
echo   Collage Studio launcher
echo   =======================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Target "%TARGET%"

if errorlevel 1 (
  echo.
  echo   Something went wrong. Scroll up for the [fail] lines.
  echo.
)

echo.
pause
endlocal
