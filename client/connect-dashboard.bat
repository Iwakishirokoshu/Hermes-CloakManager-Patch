@echo off
call "%~dp0config.bat" 2>nul || call "%~dp0config.bat.example"
start http://localhost:%PORT_HERMES_DASH%/
ssh -i "%SSH_KEY%" -L %PORT_HERMES_DASH%:127.0.0.1:9119 -N %VPS_USER%@%VPS_HOST%