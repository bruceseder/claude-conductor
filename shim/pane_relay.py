"""Per-pane named pipe relay for Claude Conductor.

Launched as the process inside each Windows Terminal pane. Spawns an interactive
shell that inherits the terminal fully, then listens on a named pipe for
send-keys commands which are injected via WriteConsoleInput.

This gives Claude Code (launched via send-keys) full terminal access for
spinners, color output, and raw-mode input.

Usage:
    python pane_relay.py --pane-id %1 --pipe-name claude-conductor-pane-1
"""

import argparse
import ctypes
import ctypes.wintypes
import json
import os
import subprocess
import sys
import threading
import time

import pywintypes
import win32file
import win32pipe

PIPE_BUFFER_SIZE = 4096
CONDUCTOR_PIPE = r"\\.\pipe\claude-conductor"

# Win32 console constants
STD_INPUT_HANDLE = -10
KEY_EVENT = 0x0001

kernel32 = ctypes.windll.kernel32


class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [
        ("bKeyDown", ctypes.wintypes.BOOL),
        ("wRepeatCount", ctypes.wintypes.WORD),
        ("wVirtualKeyCode", ctypes.wintypes.WORD),
        ("wVirtualScanCode", ctypes.wintypes.WORD),
        ("uChar", ctypes.c_wchar),
        ("dwControlKeyState", ctypes.wintypes.DWORD),
    ]


class INPUT_RECORD_UNION(ctypes.Union):
    _fields_ = [
        ("KeyEvent", KEY_EVENT_RECORD),
        # Other event types exist but we only need KeyEvent
        ("_padding", ctypes.c_byte * 16),
    ]


class INPUT_RECORD(ctypes.Structure):
    _fields_ = [
        ("EventType", ctypes.wintypes.WORD),
        ("Event", INPUT_RECORD_UNION),
    ]


def write_console_input(text: str):
    """Inject text into the console input buffer via WriteConsoleInputW.

    This types text as if the user pressed keys, so the shell (cmd.exe)
    or any process reading from the console will see it.
    """
    h_stdin = kernel32.GetStdHandle(STD_INPUT_HANDLE)
    if h_stdin == -1:
        return False

    records = []
    for char in text:
        # Key down
        rec_down = INPUT_RECORD()
        rec_down.EventType = KEY_EVENT
        rec_down.Event.KeyEvent.bKeyDown = True
        rec_down.Event.KeyEvent.wRepeatCount = 1
        rec_down.Event.KeyEvent.wVirtualKeyCode = 0
        rec_down.Event.KeyEvent.wVirtualScanCode = 0
        rec_down.Event.KeyEvent.uChar = char
        rec_down.Event.KeyEvent.dwControlKeyState = 0
        records.append(rec_down)

        # Key up
        rec_up = INPUT_RECORD()
        rec_up.EventType = KEY_EVENT
        rec_up.Event.KeyEvent.bKeyDown = False
        rec_up.Event.KeyEvent.wRepeatCount = 1
        rec_up.Event.KeyEvent.wVirtualKeyCode = 0
        rec_up.Event.KeyEvent.wVirtualScanCode = 0
        rec_up.Event.KeyEvent.uChar = char
        rec_up.Event.KeyEvent.dwControlKeyState = 0
        records.append(rec_up)

    if not records:
        return True

    arr = (INPUT_RECORD * len(records))(*records)
    written = ctypes.wintypes.DWORD(0)
    result = kernel32.WriteConsoleInputW(
        h_stdin, arr, len(records), ctypes.byref(written)
    )
    return bool(result)


def notify_conductor(pane_id: str, child_pid: int):
    """Notify Conductor that this pane relay is ready with the child PID."""
    msg = json.dumps({
        "cmd": "pane-ready",
        "args": {"pane_id": pane_id, "child_pid": child_pid},
    }).encode("utf-8")

    max_retries = 10
    for attempt in range(max_retries):
        try:
            handle = win32file.CreateFile(
                CONDUCTOR_PIPE,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0, None,
                win32file.OPEN_EXISTING,
                0, None,
            )
            win32file.WriteFile(handle, msg)
            win32file.ReadFile(handle, 4096)
            win32file.CloseHandle(handle)
            return
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(0.15)
            else:
                print(f"[relay] Warning: could not notify Conductor", file=sys.stderr)


def pipe_listener(pipe_path: str, child_proc: subprocess.Popen, pane_id: str):
    """Listen on the named pipe for send-keys and control commands.

    send-keys text is injected into the console input buffer via
    WriteConsoleInput, so the shell (and any child process like Claude Code)
    receives it as normal keyboard input with full terminal access.
    """
    while child_proc.poll() is None:
        pipe_handle = None
        try:
            pipe_handle = win32pipe.CreateNamedPipe(
                pipe_path,
                win32pipe.PIPE_ACCESS_DUPLEX,
                (
                    win32pipe.PIPE_TYPE_MESSAGE
                    | win32pipe.PIPE_READMODE_MESSAGE
                    | win32pipe.PIPE_WAIT
                ),
                win32pipe.PIPE_UNLIMITED_INSTANCES,
                PIPE_BUFFER_SIZE,
                PIPE_BUFFER_SIZE,
                0, None,
            )

            win32pipe.ConnectNamedPipe(pipe_handle, None)

            _, data = win32file.ReadFile(pipe_handle, PIPE_BUFFER_SIZE)
            msg = json.loads(data.decode("utf-8"))

            action = msg.get("action", "")

            if action == "send-keys":
                text = msg.get("text", "")
                if text:
                    ok = write_console_input(text)
                    win32file.WriteFile(
                        pipe_handle,
                        json.dumps({"ok": ok}).encode("utf-8"),
                    )
                else:
                    win32file.WriteFile(
                        pipe_handle,
                        json.dumps({"ok": True}).encode("utf-8"),
                    )

            elif action == "kill":
                child_proc.terminate()
                win32file.WriteFile(
                    pipe_handle,
                    json.dumps({"ok": True}).encode("utf-8"),
                )

            elif action == "ping":
                win32file.WriteFile(
                    pipe_handle,
                    json.dumps({"ok": True, "pid": child_proc.pid}).encode("utf-8"),
                )

            win32pipe.DisconnectNamedPipe(pipe_handle)

        except pywintypes.error as e:
            if e.winerror != 233:
                if child_proc.poll() is None:
                    print(f"[relay] Pipe error: {e}", file=sys.stderr)
        except Exception as e:
            if child_proc.poll() is None:
                print(f"[relay] Error: {e}", file=sys.stderr)
        finally:
            if pipe_handle is not None:
                try:
                    win32file.CloseHandle(pipe_handle)
                except Exception:
                    pass


def main():
    parser = argparse.ArgumentParser(description="Pane relay for Claude Conductor")
    parser.add_argument("--pane-id", required=True, help="Pane identifier (e.g., %%1)")
    parser.add_argument("--pipe-name", required=True, help="Named pipe name (no \\\\.\\pipe\\ prefix)")
    parser.add_argument("--tmux-socket", default="", help="TMUX env var value")
    parser.add_argument("--shim-path", default="", help="Path to shim dist dir (prepended to PATH)")
    parser.add_argument("--command", nargs=argparse.REMAINDER, default=None,
                        help="Command to run instead of default shell")
    args = parser.parse_args()

    pipe_path = rf"\\.\pipe\{args.pipe_name}"
    pane_id = args.pane_id

    # Set tmux environment so Claude Code in this pane detects agent teams mode
    env = os.environ.copy()
    if args.tmux_socket:
        env["TMUX"] = args.tmux_socket
    env["TMUX_PANE"] = pane_id
    env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"
    if args.shim_path:
        env["PATH"] = f"{args.shim_path};{env.get('PATH', '')}"
    os.environ.update(env)

    # Launch either the specified command or an interactive shell.
    # No stdin/stdout/stderr capture — the process gets full ConPTY access.
    if args.command:
        # Filter out leading '--' separator if present
        cmd = [c for c in args.command if c != "--"]
        if cmd:
            child_proc = subprocess.Popen(cmd, env=env)
        else:
            shell = os.environ.get("COMSPEC", "cmd.exe")
            child_proc = subprocess.Popen([shell], env=env)
    else:
        shell = os.environ.get("COMSPEC", "cmd.exe")
        child_proc = subprocess.Popen([shell], env=env)

    print(f"[relay] Pane {pane_id} ready (shell PID: {child_proc.pid})")

    # Notify Conductor
    notify_conductor(pane_id, child_proc.pid)

    # Start the pipe listener in a background thread
    listener = threading.Thread(
        target=pipe_listener,
        args=(pipe_path, child_proc, pane_id),
        daemon=True,
    )
    listener.start()

    # Wait for child to exit
    exit_code = child_proc.wait()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
