@echo off
setlocal
cd /d "%~dp0"
if not exist ".env" (
  echo Missing .env. Place this launcher in the existing bot folder beside .env.
  exit /b 1
)
rem Use this bot folder's saved token instead of a stale inherited Windows value.
rem SETLOCAL confines this change to this bot process and its children.
set "DISCORD_TOKEN="
python -m app.bot
exit /b %ERRORLEVEL%
