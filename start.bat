@echo off
setlocal
cd /d "%~dp0"
if not exist .venv (
  py -3 -m venv .venv
)
call .venv\Scripts\activate
python -m pip install -r backend\requirements.txt
if exist frontend\package.json (
  cd frontend
  if not exist node_modules call npm install
  call npm run build
  cd ..
)
set CBC_PORT=8787
python run.py --host 127.0.0.1 --port 8787
