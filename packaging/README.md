# Windows packaging

1. Install Python 3.10+ and Node.js 18+.
2. Build the UI: `cd frontend && npm install && npm run build`
3. Install PyInstaller: `python -m pip install pyinstaller`
4. From the repo root:

```
pyinstaller packaging/CryptoBotControl.spec
```

The output folder `dist/CryptoBotControl` contains `CryptoBotControl.exe`.

Users who prefer not to freeze the app can run `start.bat` instead.
