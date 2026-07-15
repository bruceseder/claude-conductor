"""Launch Claude Conductor + a lead agent terminal for Agent Teams.

Usage:
    python launch_team.py [working_directory]

This script:
1. Starts Conductor (widget + shim server) in the background
2. Opens a Windows Terminal with the right env vars for Claude Code
   to detect it's inside a tmux-compatible session
3. You then type 'claude' in that terminal and tell it to create a team
"""

import os
import subprocess
import sys
import threading
import time

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

SHIM_DIR = os.path.join(PROJECT_ROOT, "shim")
SHIM_DIST_DIR = os.path.join(SHIM_DIR, "dist")


def main():
    working_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()

    # Build the environment for the lead terminal
    env = os.environ.copy()

    # Prepend shim dist dir to PATH so tmux.exe is found first
    # Use forward slashes — Node.js spawnSync needs them on Windows
    dist_path = SHIM_DIST_DIR.replace("\\", "/")
    shim_path = SHIM_DIR.replace("\\", "/")
    env["PATH"] = f"{dist_path};{shim_path};{env.get('PATH', '')}"

    # Claude Code checks TMUX env var AND verifies the socket file exists.
    # On Windows, /tmp resolves to C:\tmp in Node.js. Create the fake socket
    # file so the existence check passes.
    socket_dir = os.path.join(os.environ.get("SystemDrive", "C:"), "tmp")
    os.makedirs(socket_dir, exist_ok=True)
    socket_path = os.path.join(socket_dir, "conductor-shim")
    # Create or touch the socket file
    with open(socket_path, "a"):
        pass
    # Use forward-slash path for TMUX (Unix convention)
    tmux_socket = socket_path.replace("\\", "/")

    env["TMUX"] = f"{tmux_socket},{os.getpid()},0"
    env["TMUX_PANE"] = "%0"

    print(f"Shim dir: {SHIM_DIR}")
    print(f"Working dir: {working_dir}")
    print(f"TMUX={env['TMUX']}")
    print(f"TMUX_PANE={env['TMUX_PANE']}")
    print()

    # Verify shim works
    tmux_exe = os.path.join(SHIM_DIST_DIR, "tmux.exe")
    if not os.path.isfile(tmux_exe):
        print(f"ERROR: tmux.exe not found at {tmux_exe}")
        print("Run: python shim/build_shim.py")
        sys.exit(1)

    r = subprocess.run(
        [tmux_exe, "-V"],
        capture_output=True, text=True, env=env,
    )
    if r.stdout.strip() != "tmux 3.4":
        print(f"ERROR: tmux shim not working. Got: {r.stdout!r}")
        sys.exit(1)
    print(f"tmux shim verified: {r.stdout.strip()}")

    # Start Conductor in a background thread
    print("Starting Conductor...")

    from widget.utils import setup_dpi_awareness
    setup_dpi_awareness()

    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    from widget.app import App
    app = App()

    # Wait for shim server to be ready
    time.sleep(1)

    # Launch the lead terminal with env vars
    print("Launching lead terminal...")
    print("=" * 50)
    print("  Type 'claude' in the terminal that opens")
    print("  Then ask Claude to create an agent team")
    print("=" * 50)

    # Register the lead pane in session manager BEFORE launching WT
    # so we can use session.window_name for the -w flag
    session = app._session_mgr.get_or_create_session("default")
    pane_id = app._session_mgr.allocate_pane_id(session)
    app._session_mgr.register_pane(session, pane_id, working_dir=working_dir, role="lead")

    # WT doesn't inherit caller's env — write a temp batch file that sets them
    dist_path = SHIM_DIST_DIR.replace("/", "\\")
    shim_path = SHIM_DIR.replace("/", "\\")
    batch_file = os.path.join(PROJECT_ROOT, "_team_env.cmd")

    with open(batch_file, "w") as f:
        f.write("@echo off\n")
        f.write(f'set "TMUX={tmux_socket},{os.getpid()},0"\n')
        f.write(f'set "TMUX_PANE=%%0"\n')
        f.write(f'set "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1"\n')
        f.write(f'set "PATH={dist_path};{shim_path};%PATH%"\n')
        f.write('echo.\n')
        f.write('echo Environment ready. Type: claude\n')
        f.write('echo.\n')
        f.write("cmd /k\n")

    cmd = [
        "wt.exe",
        "-w", session.window_name,  # Must match session.window_name for split-pane targeting
        "new-tab",
        "--title", "Claude Team: Lead",
        "-d", working_dir,
        batch_file,
    ]

    print(f"Launching: {' '.join(cmd)}")
    subprocess.Popen(cmd)

    # Find and track the container HWND after a delay
    def find_container():
        time.sleep(2)
        from widget.wt_integration import _find_window_by_title
        hwnd = _find_window_by_title("Claude Team: Lead")
        if hwnd:
            session.container_hwnd = hwnd
            print(f"Lead terminal HWND: {hwnd}")

    threading.Thread(target=find_container, daemon=True).start()

    # Run the Conductor event loop
    app.run()


if __name__ == "__main__":
    main()
