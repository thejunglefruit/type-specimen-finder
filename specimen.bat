@echo off
rem SPDX-License-Identifier: GPL-3.0-or-later
rem Copyright (C) 2026 The Jungle Fruit
rem Double-click launcher for Windows: asks for a word, builds the specimen book and opens it.
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3 from https://www.python.org and tick "Add to PATH".
  pause
  exit /b 1
)

python -c "import PIL, fontTools" >nul 2>nul
if errorlevel 1 (
  echo Installing the requirements, only the first time...
  python -m pip install -r requirements.txt
)

set "WORD="
set /p WORD="Sample word [Specimen]: "
if "%WORD%"=="" set "WORD=Specimen"
set "STEM="
set /p STEM="Minimum stem, 0.33 = black weights only [Enter = default]: "

if "%STEM%"=="" (
  python specimen.py "%WORD%" --out "%~dp0out"
) else (
  python specimen.py "%WORD%" --min-stem %STEM% --out "%~dp0out"
)
if errorlevel 1 (
  echo Something went wrong: see the messages above.
  pause
  exit /b 1
)

if exist "%~dp0out\%WORD%_specimen.pdf" start "" "%~dp0out\%WORD%_specimen.pdf"
explorer "%~dp0out"
endlocal
