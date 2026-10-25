@echo off
rem meetingVAR desktop shell build (deps: D:\env\ui-venv). Run from anywhere.
chcp 65001 >nul
pushd "%~dp0..\.."
set UIPY=D:\env\ui-venv\python.exe
"%UIPY%" desktop\assets\make_icon.py || goto err
rem NOTE: conda 的 _ctypes.pyd 依赖 Library\bin\ffi.dll（pefile 实查；PyInstaller 不自动收集），
rem pythonnet/clr_loader 需要完整收集，否则冻结后 WebView 起不来（实测踩过）。
"%UIPY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name meetingVAR --paths "src" --hidden-import meetingvar.memory.preview --hidden-import meetingvar.memory.files --hidden-import meetingvar.memory.legacy_office --hidden-import meetingvar.commercial.license --hidden-import meetingvar.commercial.updates --hidden-import meetingvar.commercial.telemetry --hidden-import meetingvar.commercial.data --hidden-import meetingvar.rag --hidden-import meetingvar.agent.memory_index --hidden-import meetingvar.agent.embeddings --hidden-import meetingvar.agent.rerank --hidden-import meetingvar.agent.memory_judge --hidden-import meetingvar.kg --hidden-import meetingvar.kg.build --hidden-import meetingvar.kg.store --hidden-import meetingvar.kg.extract --hidden-import meetingvar.kg.link --hidden-import meetingvar.kg.ingest --icon desktop\assets\icon.ico --add-data "desktop\web;web" --add-data "desktop\engine_bridge.py;." --add-data "desktop\assets\icon.ico;assets" --add-data "docs\PRIVACY.md;legal" --add-data "docs\TERMS.md;legal" --add-data "docs\THIRD_PARTY_LICENSES.md;legal" --add-binary "D:\env\ui-venv\Library\bin\ffi.dll;." --add-binary "D:\env\ui-venv\Library\bin\ffi-8.dll;." --collect-all pythonnet --collect-all clr_loader desktop\app.py || goto err
echo [build] done: dist\meetingVAR.exe
popd
exit /b 0
:err
echo [build] FAILED
popd
exit /b 1
