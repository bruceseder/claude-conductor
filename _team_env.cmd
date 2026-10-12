@echo off
set "TMUX=/tmp/conductor-shim,38060,0"
set "TMUX_PANE=%%0"
set "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1"
set "PATH=%~dp0shim\dist;%~dp0shim;%PATH%"
echo.
echo Environment ready. Type: claude
echo.
cmd /k
