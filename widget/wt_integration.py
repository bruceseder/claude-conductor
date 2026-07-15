"""Windows Terminal integration for Agent Teams.

Handles all interactions with Windows Terminal: launching containers,
splitting panes, and sending keystrokes via per-pane named pipes.
"""

import json
import logging
import os
import subprocess
import sys
import time

import pywintypes
import win32file
import win32gui

from . import config as cfg
from .session_manager import TeamSession, PaneInfo

log = logging.getLogger(__name__)

# Timeout waiting for WT window to appear (seconds)
HWND_WAIT_TIMEOUT = 5.0
HWND_POLL_INTERVAL = 0.2


def _find_shim_dir() -> str:
    """Find the absolute path to the shim directory."""
    # Look relative to this file's package root
    package_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    shim_dir = os.path.join(package_dir, cfg.SHIM_DIR)
    if os.path.isdir(shim_dir):
        return shim_dir
    return ""


def _find_window_by_title(title_fragment: str) -> int:
    """Find a window HWND by title substring match."""
    result = [0]

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if title_fragment in title:
            result[0] = hwnd
            return False  # stop enumeration
        return True

    try:
        win32gui.EnumWindows(callback, None)
    except pywintypes.error:
        pass  # EnumWindows raises when callback returns False
    return result[0]


def launch_container(session: TeamSession, working_dir: str) -> int:
    """Launch a named Windows Terminal window and return its HWND.

    Uses -w to create a named window that subsequent split-pane
    commands can target reliably.
    """
    title = f"Claude Team: {session.session_name}"
    cmd = [
        "wt.exe",
        "-w", session.window_name,
        "new-tab",
        "--title", title,
        "-d", working_dir,
    ]

    log.info("Launching container: %s", " ".join(cmd))
    subprocess.Popen(cmd)

    # Wait for window to appear
    hwnd = _wait_for_hwnd(title)
    if hwnd:
        session.container_hwnd = hwnd
        log.info("Container HWND: %d for session '%s'", hwnd, session.session_name)
    else:
        log.warning("Could not find container HWND for session '%s'", session.session_name)

    return hwnd


def _wait_for_hwnd(title_fragment: str) -> int:
    """Poll for a window with the given title to appear."""
    deadline = time.time() + HWND_WAIT_TIMEOUT
    while time.time() < deadline:
        hwnd = _find_window_by_title(title_fragment)
        if hwnd:
            return hwnd
        time.sleep(HWND_POLL_INTERVAL)
    return 0


def split_pane(session: TeamSession, pane: PaneInfo, horizontal: bool,
               command: list[str] | None = None) -> bool:
    """Split a pane in the container window.

    Each pane runs pane_relay.py which creates a per-pane named pipe
    for keystroke injection.

    Args:
        session: The team session
        pane: The PaneInfo for the new pane being created
        horizontal: True = side by side (tmux -h), False = stacked (tmux -v)
        command: Optional command to run in the pane instead of default shell
    """
    shim_dir = _find_shim_dir()
    relay_script = os.path.join(shim_dir, "pane_relay.py")

    if not os.path.isfile(relay_script):
        log.error("pane_relay.py not found at %s", relay_script)
        return False

    pipe_name = f"claude-conductor-pane-{pane.pane_id.lstrip('%')}"
    dist_dir = os.path.join(shim_dir, "dist")

    # Build TMUX socket value for child panes
    tmux_socket = os.environ.get("TMUX", f"/tmp/conductor-shim,{os.getpid()},0")

    # WT flag mapping (opposite of tmux):
    # tmux -h (horizontal) = panes side by side = WT -V (vertical split line)
    # tmux -v (vertical) = panes stacked = WT -H (horizontal split line)
    wt_flag = "-V" if horizontal else "-H"

    cmd = [
        "wt.exe", "-w", session.window_name,
        "split-pane",
        wt_flag,
        "-d", pane.working_dir or os.getcwd(),
        "--title", f"Pane {pane.pane_id}",
        "python", relay_script,
        "--pane-id", pane.pane_id,
        "--pipe-name", pipe_name,
        "--tmux-socket", tmux_socket,
        "--shim-path", f"{dist_dir};{shim_dir}",
    ]

    # Pass through the command to run in the pane
    if command:
        cmd.extend(["--command", "--"] + command)

    log.info("Splitting pane: %s", " ".join(cmd))

    try:
        subprocess.Popen(cmd)
        return True
    except Exception as e:
        log.error("Failed to split pane: %s", e)
        return False


def send_keys_to_pane(pane: PaneInfo, keys: list[str]) -> bool:
    """Send keystrokes to a pane via its named pipe relay.

    Args:
        pane: Target pane
        keys: List of key strings. "Enter" is translated to newline.
    """
    pipe_name = f"claude-conductor-pane-{pane.pane_id.lstrip('%')}"
    pipe_path = rf"\\.\pipe\{pipe_name}"

    # Build the text to send
    text_parts = []
    for key in keys:
        if key == "Enter":
            text_parts.append("\n")
        elif key == "Escape":
            text_parts.append("\x1b")
        elif key == "Tab":
            text_parts.append("\t")
        elif key == "Space":
            text_parts.append(" ")
        elif key == "BSpace":
            text_parts.append("\x08")
        else:
            text_parts.append(key)
    text = "".join(text_parts)

    msg = json.dumps({"action": "send-keys", "text": text}).encode("utf-8")

    # Retry: the relay pipe may not be ready immediately after pane creation
    max_retries = 10
    retry_delay = 0.3

    for attempt in range(max_retries):
        try:
            handle = win32file.CreateFile(
                pipe_path,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0, None,
                win32file.OPEN_EXISTING,
                0, None,
            )
            win32file.WriteFile(handle, msg)
            _, response = win32file.ReadFile(handle, 4096)
            win32file.CloseHandle(handle)
            result = json.loads(response.decode("utf-8"))
            return result.get("ok", False)
        except pywintypes.error as e:
            if attempt < max_retries - 1:
                log.debug("Pipe not ready for pane %s (attempt %d): %s",
                         pane.pane_id, attempt + 1, e)
                time.sleep(retry_delay)
            else:
                log.warning("Failed to send keys to pane %s after %d attempts: %s",
                           pane.pane_id, max_retries, e)
                return False
        except Exception as e:
            log.warning("Failed to send keys to pane %s: %s", pane.pane_id, e)
            return False
    return False


def kill_pane_process(pane: PaneInfo) -> bool:
    """Kill a pane's relay process via its named pipe."""
    pipe_name = f"claude-conductor-pane-{pane.pane_id.lstrip('%')}"
    pipe_path = rf"\\.\pipe\{pipe_name}"

    msg = json.dumps({"action": "kill"}).encode("utf-8")

    try:
        handle = win32file.CreateFile(
            pipe_path,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0, None,
            win32file.OPEN_EXISTING,
            0, None,
        )
        win32file.WriteFile(handle, msg)
        _, response = win32file.ReadFile(handle, 4096)
        win32file.CloseHandle(handle)
        return True
    except Exception as e:
        log.warning("Failed to kill pane %s: %s", pane.pane_id, e)
        return False


def get_container_env(session: TeamSession, pane_id: str = "%0") -> dict:
    """Get environment variables needed for the lead session.

    These must be set in the lead terminal for Claude Code
    to detect it's inside a tmux-compatible session.
    """
    shim_dir = _find_shim_dir()
    dist_dir = os.path.join(shim_dir, "dist")

    # Prepend shim dist dir to PATH so our tmux.exe is found first
    path = os.environ.get("PATH", "")
    if shim_dir and os.path.isdir(dist_dir):
        new_path = f"{dist_dir};{path}"
    elif shim_dir:
        # Fallback: use shim dir itself (for running tmux.py directly)
        new_path = f"{shim_dir};{path}"
    else:
        new_path = path

    return {
        "TMUX": f"/tmp/conductor-shim,{os.getpid()},0",
        "TMUX_PANE": pane_id,
        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
        "PATH": new_path,
    }
