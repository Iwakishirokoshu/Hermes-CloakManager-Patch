@echo off
call "%~dp0config.bat" 2>nul || call "%~dp0config.bat.example"
ssh -i "%SSH_KEY%" -L %PORT_CLOAK_MANAGER%:127.0.0.1:8080 -N %VPS_USER%@%VPS_HOST%