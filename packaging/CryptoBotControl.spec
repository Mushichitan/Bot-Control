# PyInstaller spec. Build on Windows: pyinstaller packaging/CryptoBotControl.spec
a = Analysis(
    ['../run.py'],
    pathex=['../backend'],
    binaries=[],
    datas=[('../frontend/dist', 'frontend/dist'), ('../mock_bot', 'mock_bot')],
    hiddenimports=['uvicorn.logging', 'uvicorn.protocols.http.auto', 'crypto_bot_control'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
)
pyz = PYZ(a.pure, a.zipped_data)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='CryptoBotControl',
    debug=False,
    upx=True,
    console=False,
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name='CryptoBotControl')
