@echo off
REM Builds dist\SmartMark\ - a folder with SmartMark.exe that teachers can copy and run without Python.
REM Run from Command Prompt inside the project folder:   build_exe.bat
cd /d "%~dp0"
if exist E:\ set PIP_CACHE_DIR=E:\pip-cache

if not exist .venv\Scripts\python.exe (
    echo .venv not found - set up the project first ^(README step 1^).
    exit /b 1
)
if not exist models\digit_cnn.onnx (
    echo models\digit_cnn.onnx not found - train the model first ^(README step 10^).
    exit /b 1
)

echo [1/3] Installing the packaging tool...
.venv\Scripts\python.exe -m pip install --only-binary=:all: -q -r requirements.txt pyinstaller || goto :fail

echo [2/3] Building SmartMark.exe ^(takes a few minutes^)...
.venv\Scripts\python.exe -m PyInstaller --noconfirm --windowed --name SmartMark --paths scripts ^
  --hidden-import 09_read_sheets --hidden-import 10_fill_excel --hidden-import 05_evaluate_m2 ^
  --collect-all customtkinter --workpath build --distpath dist scripts\smartmark_app.py || goto :fail

echo [3/3] Adding the template, model and teacher guide...
xcopy templates dist\SmartMark\templates\ /E /I /Y /Q >nul || goto :fail
if not exist dist\SmartMark\models mkdir dist\SmartMark\models
copy /Y models\digit_cnn.onnx dist\SmartMark\models\ >nul || goto :fail
copy /Y models\digit_model.json dist\SmartMark\models\ >nul || goto :fail
copy /Y docs\TEACHER_GUIDE.md "dist\SmartMark\How to use SmartMark.txt" >nul

echo.
echo Done:  dist\SmartMark\SmartMark.exe
echo Give teachers the whole dist\SmartMark folder ^(zip it^). Results are saved inside that folder.
exit /b 0

:fail
echo.
echo Build FAILED - see the messages above.
exit /b 1
