@echo off
rem meetingVAR installer build (needs dist\meetingVAR.exe first). Run from anywhere.
chcp 65001 >nul
pushd "%~dp0..\.."
set UIPY=D:\env\ui-venv\python.exe
"%UIPY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name meetingVAR-setup --icon desktop\assets\icon.ico --add-data "dist\meetingVAR.exe;payload" --add-data "desktop\assets\icon.ico;assets" desktop\build\setup_app.py || goto err
if not exist dist_installer mkdir dist_installer
copy /y dist\meetingVAR-setup.exe dist_installer\meetingVAR-setup-0.13.0.exe >nul
echo [setup] done: dist_installer\meetingVAR-setup-0.13.0.exe
popd
exit /b 0
:err
echo [setup] FAILED
popd
exit /b 1
