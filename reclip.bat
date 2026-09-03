@echo off
@rem ============================================================================
@rem  ReClip Plus - Windows One-Click Setup & Launch Script
@rem  High-Performance Self-Hosted Media Downloader
@rem ============================================================================
setlocal EnableExtensions EnableDelayedExpansion

title ReClip Plus - Media Downloader
set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%"
chcp 65001 >nul 2>&1

rem Default configurations
if not defined PORT set "PORT=8899"
if not defined HOST set "HOST=0.0.0.0"
set "SETUP_ONLY=0"
set "NO_BROWSER=0"
set "DO_UPDATE=0"
set "FORCE_INSTALL=0"
set "NON_INTERACTIVE=0"

if defined CI set "NON_INTERACTIVE=1"

rem ----------------------------------------------------------------------------
rem Step 0: Parse Command Line Arguments
rem ----------------------------------------------------------------------------
:PARSE_ARGS
if "%~1"=="" goto :ARGS_DONE
if /i "%~1"=="--help" goto :SHOW_HELP
if /i "%~1"=="-h" goto :SHOW_HELP
if /i "%~1"=="/?" goto :SHOW_HELP
if /i "%~1"=="--setup-only" (
    set "SETUP_ONLY=1"
    shift
    goto :PARSE_ARGS
)
if /i "%~1"=="--no-browser" (
    set "NO_BROWSER=1"
    shift
    goto :PARSE_ARGS
)
if /i "%~1"=="--update" (
    set "DO_UPDATE=1"
    shift
    goto :PARSE_ARGS
)
if /i "%~1"=="--install" (
    set "FORCE_INSTALL=1"
    shift
    goto :PARSE_ARGS
)
if /i "%~1"=="--non-interactive" (
    set "NON_INTERACTIVE=1"
    shift
    goto :PARSE_ARGS
)
if /i "%~1"=="-y" (
    set "NON_INTERACTIVE=1"
    shift
    goto :PARSE_ARGS
)
if /i "%~1"=="--port" (
    set "PORT=%~2"
    shift
    shift
    goto :PARSE_ARGS
)
if /i "%~1"=="-p" (
    set "PORT=%~2"
    shift
    shift
    goto :PARSE_ARGS
)
shift
goto :PARSE_ARGS
:ARGS_DONE

echo ======================================================================
echo   ReClip Plus - Initializing Windows Environment
echo ======================================================================
echo.

rem ----------------------------------------------------------------------------
rem Step 1: Detect Python 3.10+ and Virtual Environment
rem ----------------------------------------------------------------------------
set "VENV_DIR=%PROJECT_ROOT%venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "VENV_PIP=%VENV_DIR%\Scripts\pip.exe"
set "PYTHON_VER="
set "VENV_READY="

rem Fast check: does a working virtual environment already exist?
if exist "%VENV_PYTHON%" (
    "%VENV_PYTHON%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if !errorlevel! equ 0 (
        set "VENV_READY=1"
        for /f "tokens=2" %%v in ('"%VENV_PYTHON%" --version 2^>^&1') do set "PYTHON_VER=%%v"
        goto :PYTHON_DETECTED
    ) else (
        echo [!] Existing virtual environment is invalid or outdated. Rebuilding...
        rmdir /s /q "%VENV_DIR%" >nul 2>&1
    )
)

rem Virtualenv not ready; locate base Python 3.10+
set "BASE_PYTHON="

rem 1.1 Python Launcher
py -3 -c "import sys; sys.exit(0)" >nul 2>&1
if !errorlevel! equ 0 (
    set BASE_PYTHON=py -3
    goto :CHECK_BASE_VERSION
)

rem 1.2 python in PATH
python -c "import sys; sys.exit(0)" >nul 2>&1
if !errorlevel! equ 0 (
    set BASE_PYTHON=python
    goto :CHECK_BASE_VERSION
)

rem 1.3 python3 in PATH
python3 -c "import sys; sys.exit(0)" >nul 2>&1
if !errorlevel! equ 0 (
    set BASE_PYTHON=python3
    goto :CHECK_BASE_VERSION
)

rem 1.4 Standard Windows user and machine installation paths
for %%v in (313 312 311 310) do (
    if not defined BASE_PYTHON (
        if exist "%LOCALAPPDATA%\Programs\Python\Python%%v\python.exe" (
            set BASE_PYTHON="%LOCALAPPDATA%\Programs\Python\Python%%v\python.exe"
        )
    )
    if not defined BASE_PYTHON (
        if exist "C:\Program Files\Python%%v\python.exe" (
            set BASE_PYTHON="C:\Program Files\Python%%v\python.exe"
        )
    )
)

:CHECK_BASE_VERSION
if not defined BASE_PYTHON goto :ERR_PYTHON_NOT_FOUND

rem Verify base Python version is >= 3.10
!BASE_PYTHON! -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if !errorlevel! neq 0 goto :ERR_PYTHON_TOO_OLD

for /f "tokens=2" %%v in ('!BASE_PYTHON! --version 2^>^&1') do set "PYTHON_VER=%%v"

:PYTHON_DETECTED
echo [OK] Python !PYTHON_VER! detected.

rem ----------------------------------------------------------------------------
rem Step 2: Setup Virtual Environment (.venv / venv)
rem ----------------------------------------------------------------------------
if not defined VENV_READY (
    echo [*] Creating isolated virtual environment in .\venv...
    !BASE_PYTHON! -m venv "%VENV_DIR%"
    if !errorlevel! neq 0 goto :ERR_VENV_CREATE
    if not exist "%VENV_PYTHON%" goto :ERR_VENV_CREATE
    echo [OK] Virtual environment created.
)

rem ----------------------------------------------------------------------------
rem Step 3: Detect or Locate FFmpeg
rem ----------------------------------------------------------------------------
set "FFMPEG_BIN="

rem 3.1 Check system PATH
where ffmpeg >nul 2>&1
if !errorlevel! equ 0 (
    for /f "tokens=*" %%i in ('where ffmpeg 2^>nul') do (
        if not defined FFMPEG_BIN set "FFMPEG_BIN=%%i"
    )
)

rem 3.2 Check project root and subdirectories
if not defined FFMPEG_BIN (
    if exist "%PROJECT_ROOT%ffmpeg.exe" (
        set "FFMPEG_BIN=%PROJECT_ROOT%ffmpeg.exe"
        set "PATH=%PROJECT_ROOT%;!PATH!"
    ) else if exist "%PROJECT_ROOT%bin\ffmpeg.exe" (
        set "FFMPEG_BIN=%PROJECT_ROOT%bin\ffmpeg.exe"
        set "PATH=%PROJECT_ROOT%bin;!PATH!"
    ) else if exist "%PROJECT_ROOT%ffmpeg\bin\ffmpeg.exe" (
        set "FFMPEG_BIN=%PROJECT_ROOT%ffmpeg\bin\ffmpeg.exe"
        set "PATH=%PROJECT_ROOT%ffmpeg\bin;!PATH!"
    )
)

rem 3.3 Check package manager standard install paths
if not defined FFMPEG_BIN (
    if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe" (
        set "FFMPEG_BIN=%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"
        set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;!PATH!"
    ) else if exist "C:\ProgramData\chocolatey\bin\ffmpeg.exe" (
        set "FFMPEG_BIN=C:\ProgramData\chocolatey\bin\ffmpeg.exe"
        set "PATH=C:\ProgramData\chocolatey\bin;!PATH!"
    ) else if exist "%USERPROFILE%\scoop\shims\ffmpeg.exe" (
        set "FFMPEG_BIN=%USERPROFILE%\scoop\shims\ffmpeg.exe"
        set "PATH=%USERPROFILE%\scoop\shims;!PATH!"
    )
)

if defined FFMPEG_BIN (
    echo [OK] FFmpeg found: !FFMPEG_BIN!
) else (
    echo [!] WARNING: FFmpeg was not found in PATH or project directory.
    if "%NON_INTERACTIVE%"=="1" (
        echo [!] Non-interactive mode: Skipping FFmpeg installation prompt.
    ) else (
        where winget >nul 2>&1
        if !errorlevel! equ 0 (
            echo.
            echo     FFmpeg is required to merge high-resolution video/audio formats.
            set /p "INSTALL_FFMPEG=    Would you like to install FFmpeg automatically via winget? (Y/N): "
            if /i "!INSTALL_FFMPEG!"=="Y" (
                echo [*] Installing FFmpeg via winget - please grant permission if prompted...
                winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
                where ffmpeg >nul 2>&1
                if !errorlevel! equ 0 (
                    for /f "tokens=*" %%i in ('where ffmpeg 2^>nul') do (
                        if not defined FFMPEG_BIN set "FFMPEG_BIN=%%i"
                    )
                ) else if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe" (
                    set "FFMPEG_BIN=%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"
                    set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;!PATH!"
                )
                if defined FFMPEG_BIN (
                    echo [OK] FFmpeg installed successfully: !FFMPEG_BIN!
                ) else (
                    echo [!] Automated installation finished. If ffmpeg is not yet in PATH,
                    echo     please restart this script after restarting your terminal.
                )
            )
        )
    )
    if not defined FFMPEG_BIN (
        echo [!] Continuing without FFmpeg. Note: Video audio-merging or MP3 conversion may fail.
    )
)

rem ----------------------------------------------------------------------------
rem Step 4: Install Dependencies (Fast Cached Check)
rem ----------------------------------------------------------------------------
if not exist "%PROJECT_ROOT%requirements.txt" goto :ERR_NO_REQUIREMENTS

set "SKIP_PIP=0"
if "%FORCE_INSTALL%"=="1" (
    set "SKIP_PIP=0"
) else (
    if exist "%VENV_DIR%\.installed" (
        "%VENV_PYTHON%" -c "import flask, yt_dlp" >nul 2>&1
        if !errorlevel! equ 0 set "SKIP_PIP=1"
    )
)

if "!SKIP_PIP!"=="1" (
    echo [OK] Python dependencies satisfied [cached].
) else (
    echo [*] Checking and installing required packages from requirements.txt...
    "%VENV_PYTHON%" -m pip install --disable-pip-version-check -r "%PROJECT_ROOT%requirements.txt"
    if !errorlevel! neq 0 goto :ERR_PIP_INSTALL
    echo [OK] All dependencies satisfied.
    echo installed > "%VENV_DIR%\.installed" 2>nul
)

rem ----------------------------------------------------------------------------
rem Step 5: Optional yt-dlp Update
rem ----------------------------------------------------------------------------
if /i "%DO_UPDATE%"=="1" goto :DO_UPDATE
if /i "%RECLIP_UPDATE_ON_STARTUP%"=="1" goto :DO_UPDATE
if /i "%RECLIP_UPDATE_ON_STARTUP%"=="true" goto :DO_UPDATE
goto :AFTER_UPDATE

:DO_UPDATE
if not defined RECLIP_NO_UPDATE (
    echo [*] Checking for yt-dlp updates...
    "%VENV_PYTHON%" -m pip install --quiet --upgrade yt-dlp 2>nul
    if !errorlevel! equ 0 (
        echo [OK] yt-dlp updated to the latest version.
    ) else (
        echo [!] Could not update yt-dlp. Continuing with installed version.
    )
)
:AFTER_UPDATE

rem ----------------------------------------------------------------------------
rem Step 6: Verify / Create Downloads Directory
rem ----------------------------------------------------------------------------
if not exist "%PROJECT_ROOT%downloads" (
    mkdir "%PROJECT_ROOT%downloads" >nul 2>&1
    if !errorlevel! neq 0 goto :ERR_DOWNLOADS_DIR
)

rem ----------------------------------------------------------------------------
rem Step 7: Check if setup-only mode requested
rem ----------------------------------------------------------------------------
if "%SETUP_ONLY%"=="1" (
    echo.
    echo ======================================================================
    echo   [OK] ReClip Plus setup completed successfully.
    echo ======================================================================
    exit /b 0
)

rem ----------------------------------------------------------------------------
rem Step 8: Port & Network Verification
rem ----------------------------------------------------------------------------
netstat -ano 2>nul | findstr /R /C:":%PORT% .*LISTENING" >nul 2>&1
if !errorlevel! equ 0 (
    echo.
    echo [^^!] WARNING: Port %PORT% appears to already be in use.
    echo     If ReClip Plus fails to bind, you can specify another port:
    echo     reclip.bat --port 8900
    echo.
)

rem ----------------------------------------------------------------------------
rem Step 9: Launch ReClip Plus & Open Browser
rem ----------------------------------------------------------------------------
echo.
echo ======================================================================
echo   ReClip Plus is running.
echo ======================================================================
echo.
echo   Local Web UI:  http://localhost:%PORT%
echo   Network Host:  %HOST%:%PORT%
echo   Download Dir:  %PROJECT_ROOT%downloads
echo.
echo   [Tip] Press CTRL+C in this terminal window to stop the server.
echo ======================================================================
echo.

rem Launch browser automatically after 1s delay so server binds first
if not "%NO_BROWSER%"=="1" if not "%RECLIP_NO_BROWSER%"=="1" if not "%NON_INTERACTIVE%"=="1" (
    start "" /min cmd /c "ping -n 2 127.0.0.1 >nul & start http://localhost:%PORT%"
)

rem Launch Flask server
"%VENV_PYTHON%" "%PROJECT_ROOT%app.py"
set "EXIT_CODE=!errorlevel!"

rem Handle exit codes:
rem   0           = graceful Python exit
rem   -1073741510 = Windows STATUS_CONTROL_C_EXIT (signed 32-bit: 0xC000013A)
rem   3221225786  = Windows STATUS_CONTROL_C_EXIT (unsigned 32-bit: 0xC000013A)
rem   130         = Standard SIGINT exit code (128 + 2)
if !EXIT_CODE! equ 0 goto :SHUTDOWN_OK
if !EXIT_CODE! equ -1073741510 goto :SHUTDOWN_OK
if !EXIT_CODE! equ 3221225786 goto :SHUTDOWN_OK
if !EXIT_CODE! equ 130 goto :SHUTDOWN_OK

rem If unexpected non-zero exit code:
goto :ERR_APP_CRASH

:SHUTDOWN_OK
echo.
echo [OK] ReClip Plus shut down gracefully.
exit /b 0

rem ============================================================================
rem  HELP SCREEN
rem ============================================================================
:SHOW_HELP
echo ======================================================================
echo   ReClip Plus - Windows One-Click Setup and Launch Script
echo ======================================================================
echo.
echo   Usage:
echo     reclip.bat [options]
echo.
echo   Options:
echo     --setup-only        Set up virtual environment and dependencies, then exit
echo     --no-browser        Do not automatically open browser on startup
echo     --update            Update yt-dlp to the latest version before launch
echo     --install           Force reinstall/update requirements.txt packages
echo     --port, -p [port]   Specify custom HTTP port (default: 8899)
echo     --non-interactive   Disable user prompts and exit pauses (CI/CD mode)
echo     -y                  Alias for --non-interactive
echo     --help, -h          Show this help message

echo.
echo   Environment Variables:
echo     PORT                       HTTP listen port (default: 8899)
echo     HOST                       HTTP bind host (default: 0.0.0.0)
echo     RECLIP_NO_BROWSER          Set to 1 to suppress browser launch
echo     RECLIP_UPDATE_ON_STARTUP   Set to 1 to auto-update yt-dlp on boot
echo.
exit /b 0

rem ============================================================================
rem  GLOBAL ERROR HANDLERS
rem ============================================================================

:ERR_PYTHON_NOT_FOUND
setlocal DisableDelayedExpansion
echo.
echo ======================================================================
echo   [ERROR] Python is not installed or not in your system PATH!
echo ======================================================================
echo.
echo   ReClip Plus requires Python 3.10 or newer to run.
echo.
where winget >nul 2>&1
if %errorlevel% equ 0 if not "%NON_INTERACTIVE%"=="1" (
    set /p "INSTALL_PY=  Would you like to install Python 3.11 automatically via winget? (Y/N): "
    if /i "!INSTALL_PY!"=="Y" (
        echo [*] Installing Python 3.11 via winget...
        winget install --id Python.Python.3.11 -e --accept-source-agreements --accept-package-agreements
        echo.
        echo [OK] Python installation completed. Please re-run reclip.bat.
        goto :PAUSE_AND_EXIT
    )
)
echo   Manual Installation:
echo   1. Download Python 3.11+ from https://www.python.org/downloads/
echo   2. Run installer and ensure you check:
echo      [x] "Add python.exe to PATH"
echo.
goto :PAUSE_AND_EXIT

:ERR_PYTHON_TOO_OLD
set "DETECTED_VER=%PYTHON_VER%"
setlocal DisableDelayedExpansion
echo.
echo ======================================================================
echo   [ERROR] Outdated Python version detected!
echo ======================================================================
echo.
echo   ReClip Plus requires Python 3.10 or newer (detected: %DETECTED_VER%).
echo   Please upgrade your Python installation from https://www.python.org/
echo.
goto :PAUSE_AND_EXIT

:ERR_VENV_CREATE
setlocal DisableDelayedExpansion
echo.
echo ======================================================================
echo   [ERROR] Failed to create virtual environment in .\venv!
echo ======================================================================
echo.
echo   Please verify:
echo   1. You have write permissions in this folder: %PROJECT_ROOT%
echo   2. Python venv module is available on your base Python.
echo.
goto :PAUSE_AND_EXIT

:ERR_NO_REQUIREMENTS
setlocal DisableDelayedExpansion
echo.
echo ======================================================================
echo   [ERROR] requirements.txt file was not found!
echo ======================================================================
echo.
echo   Ensure this script is placed inside the ReClip Plus project root.
echo   Current directory: %PROJECT_ROOT%
echo.
goto :PAUSE_AND_EXIT

:ERR_PIP_INSTALL
setlocal DisableDelayedExpansion
echo.
echo ======================================================================
echo   [ERROR] Failed to install required Python packages!
echo ======================================================================
echo.
echo   Troubleshooting tips:
echo   1. Check your internet connection.
echo   2. If behind a proxy or VPN, set HTTP_PROXY / HTTPS_PROXY.
echo   3. Try running manually in this directory:
echo        .\venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
goto :PAUSE_AND_EXIT

:ERR_DOWNLOADS_DIR
setlocal DisableDelayedExpansion
echo.
echo ======================================================================
echo   [ERROR] Could not create the downloads directory!
echo ======================================================================
echo.
echo   Path: %PROJECT_ROOT%downloads
echo   Check folder permissions or available disk space.
echo.
goto :PAUSE_AND_EXIT

:ERR_APP_CRASH
set "ERR_CODE=!EXIT_CODE!"
setlocal DisableDelayedExpansion
echo.
echo ======================================================================
echo   [ERROR] ReClip Plus exited unexpectedly (Exit Code: %ERR_CODE%)
echo ======================================================================
echo.
echo   Check the console logs above for tracebacks or error messages.
echo.
goto :PAUSE_AND_EXIT

:PAUSE_AND_EXIT
if "%NON_INTERACTIVE%"=="1" exit /b 1
if /i "%CI%"=="true" exit /b 1
echo.
echo ======================================================================
echo Press any key to close this window...
pause >nul
exit /b 1
