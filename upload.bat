@echo off
setlocal

set "ARDUINO_CLI=C:\Program Files\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"
set "SKETCH_DIR=%~dp0"
set "SKETCH_DIR=%SKETCH_DIR:~0,-1%"
set "FQBN=esp32:esp32:esp32"
set "PORT=COM14"
if not "%~1"=="" set "PORT=%~1"

if not exist "%ARDUINO_CLI%" (
    echo ERROR: arduino-cli.exe not found at "%ARDUINO_CLI%"
    pause
    exit /b 1
)

echo Compiling and uploading to %PORT%...
"%ARDUINO_CLI%" compile --fqbn %FQBN% "%SKETCH_DIR%"
if errorlevel 1 (
    echo.
    echo Compile FAILED. Upload aborted.
    pause
    exit /b 1
)

"%ARDUINO_CLI%" upload -p %PORT% --fqbn %FQBN% "%SKETCH_DIR%"
if errorlevel 1 (
    echo.
    echo Upload FAILED on %PORT%.
    pause
    exit /b 1
)

echo.
echo Upload SUCCEEDED on %PORT%.
pause
