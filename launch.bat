@echo off
cd /d "%~dp0"

echo Clearing output folder...
if exist "output" (
    del /q "output\*.*"
    echo Done.
) else (
    mkdir output
    echo output folder created.
)

echo.
echo Running scraper...
echo.
python main.py
