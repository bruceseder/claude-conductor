"""tmux shim for Claude Conductor.

This script impersonates tmux on Windows. Claude Code invokes it thinking
it's the real tmux binary. The shim handles simple commands locally (e.g., -V)
and forwards everything else to Conductor over a named pipe.

Compiled to tmux.exe via PyInstaller and placed on PATH before any real tmux.
"""

import json
import sys
import struct

PIPE_NAME = r"\\.\pipe\claude-conductor"
FAKE_VERSION = "tmux 3.4"


def parse_args(argv: list[str]) -> dict:
    """Parse tmux command-line arguments into a structured request.

    tmux CLI syntax:
        tmux [-L socket] [-S socket-path] command [flags...]

    We need to handle:
        -L <name>    (socket name, ignored but consumed)
        -S <path>    (socket path, ignored but consumed)
        -V           (version, handled locally)
        command      (has-session, split-window, send-keys, etc.)
        command flags (varies per command)
    """
    args = argv[1:]  # skip "tmux" itself
    idx = 0

    # Consume global flags
    while idx < len(args):
        if args[idx] == "-L" and idx + 1 < len(args):
            idx += 2  # skip -L and its value
        elif args[idx] == "-S" and idx + 1 < len(args):
            idx += 2  # skip -S and its value
        elif args[idx] == "-V":
            return {"cmd": "version", "args": {}}
        elif args[idx].startswith("-"):
            # Unknown global flag, skip
            idx += 1
        else:
            break

    if idx >= len(args):
        # No command given, just print version
        return {"cmd": "version", "args": {}}

    command = args[idx]
    cmd_args = args[idx + 1:]

    parser = COMMAND_PARSERS.get(command)
    if parser:
        return {"cmd": command, "args": parser(cmd_args)}

    # Unknown command — forward raw args
    return {"cmd": command, "args": {"raw": cmd_args}}


def parse_has_session(args: list[str]) -> dict:
    """Parse: has-session -t <target>"""
    result = {}
    idx = 0
    while idx < len(args):
        if args[idx] == "-t" and idx + 1 < len(args):
            result["target"] = args[idx + 1]
            idx += 2
        elif args[idx].startswith("-t"):
            # -t<target> (no space)
            result["target"] = args[idx][2:]
            idx += 1
        else:
            idx += 1
    return result


def parse_new_session(args: list[str]) -> dict:
    """Parse: new-session -d -s <name> [-x W] [-y H]"""
    result = {"detached": False}
    idx = 0
    while idx < len(args):
        if args[idx] == "-d":
            result["detached"] = True
            idx += 1
        elif args[idx] == "-s" and idx + 1 < len(args):
            result["session_name"] = args[idx + 1]
            idx += 2
        elif args[idx].startswith("-s"):
            result["session_name"] = args[idx][2:]
            idx += 1
        elif args[idx] == "-x" and idx + 1 < len(args):
            result["width"] = int(args[idx + 1])
            idx += 2
        elif args[idx] == "-y" and idx + 1 < len(args):
            result["height"] = int(args[idx + 1])
            idx += 2
        else:
            idx += 1
    return result


def parse_split_window(args: list[str]) -> dict:
    """Parse: split-window [-h|-v] [-c <dir>] [-F <fmt>] [-t <target>] [command...]"""
    result = {"horizontal": False}
    idx = 0
    while idx < len(args):
        if args[idx] == "-h":
            result["horizontal"] = True
            idx += 1
        elif args[idx] == "-v":
            result["horizontal"] = False
            idx += 1
        elif args[idx] == "-c" and idx + 1 < len(args):
            result["directory"] = args[idx + 1]
            idx += 2
        elif args[idx] == "-F" and idx + 1 < len(args):
            result["format"] = args[idx + 1]
            idx += 2
        elif args[idx] == "-t" and idx + 1 < len(args):
            result["target"] = args[idx + 1]
            idx += 2
        elif args[idx].startswith("-t"):
            result["target"] = args[idx][2:]
            idx += 1
        elif args[idx] == "-P":
            result["print_info"] = True
            idx += 1
        else:
            # Remaining args are the command to run in the new pane
            result["command"] = args[idx:]
            break
    return result


def parse_send_keys(args: list[str]) -> dict:
    """Parse: send-keys -t <target> <key>... [Enter]"""
    result = {"keys": []}
    idx = 0
    while idx < len(args):
        if args[idx] == "-t" and idx + 1 < len(args):
            result["target"] = args[idx + 1]
            idx += 2
        elif args[idx].startswith("-t"):
            result["target"] = args[idx][2:]
            idx += 1
        elif args[idx] == "-l":
            # literal flag
            result["literal"] = True
            idx += 1
        else:
            result["keys"].append(args[idx])
            idx += 1
    return result


def parse_kill_pane(args: list[str]) -> dict:
    """Parse: kill-pane -t <target>"""
    result = {}
    idx = 0
    while idx < len(args):
        if args[idx] == "-t" and idx + 1 < len(args):
            result["target"] = args[idx + 1]
            idx += 2
        elif args[idx].startswith("-t"):
            result["target"] = args[idx][2:]
            idx += 1
        else:
            idx += 1
    return result


def parse_list_panes(args: list[str]) -> dict:
    """Parse: list-panes [-F <fmt>] [-t <target>] [-a]"""
    result = {}
    idx = 0
    while idx < len(args):
        if args[idx] == "-F" and idx + 1 < len(args):
            result["format"] = args[idx + 1]
            idx += 2
        elif args[idx] == "-t" and idx + 1 < len(args):
            result["target"] = args[idx + 1]
            idx += 2
        elif args[idx].startswith("-t"):
            result["target"] = args[idx][2:]
            idx += 1
        elif args[idx] == "-a":
            result["all_sessions"] = True
            idx += 1
        else:
            idx += 1
    return result


def parse_list_windows(args: list[str]) -> dict:
    """Parse: list-windows [-F <fmt>] [-t <target>]"""
    result = {}
    idx = 0
    while idx < len(args):
        if args[idx] == "-F" and idx + 1 < len(args):
            result["format"] = args[idx + 1]
            idx += 2
        elif args[idx] == "-t" and idx + 1 < len(args):
            result["target"] = args[idx + 1]
            idx += 2
        elif args[idx].startswith("-t"):
            result["target"] = args[idx][2:]
            idx += 1
        else:
            idx += 1
    return result


def parse_display_message(args: list[str]) -> dict:
    """Parse: display-message [-p] [-F <fmt>] [-t <target>]"""
    result = {}
    idx = 0
    while idx < len(args):
        if args[idx] == "-p":
            result["print"] = True
            idx += 1
        elif args[idx] == "-F" and idx + 1 < len(args):
            result["format"] = args[idx + 1]
            idx += 2
        elif args[idx] == "-t" and idx + 1 < len(args):
            result["target"] = args[idx + 1]
            idx += 2
        elif args[idx].startswith("-t"):
            result["target"] = args[idx][2:]
            idx += 1
        else:
            # Positional argument is the message/format
            result["message"] = args[idx]
            idx += 1
    return result


def parse_attach_session(args: list[str]) -> dict:
    """Parse: attach-session -t <target>"""
    result = {}
    idx = 0
    while idx < len(args):
        if args[idx] == "-t" and idx + 1 < len(args):
            result["target"] = args[idx + 1]
            idx += 2
        elif args[idx].startswith("-t"):
            result["target"] = args[idx][2:]
            idx += 1
        else:
            idx += 1
    return result


def parse_list_sessions(args: list[str]) -> dict:
    """Parse: list-sessions [-F <fmt>]"""
    result = {}
    idx = 0
    while idx < len(args):
        if args[idx] == "-F" and idx + 1 < len(args):
            result["format"] = args[idx + 1]
            idx += 2
        else:
            idx += 1
    return result


def parse_new_window(args: list[str]) -> dict:
    """Parse: new-window [-d] [-n <name>] [-t <target>] [-c <dir>] [-F <fmt>] [-P] [cmd...]"""
    result = {"detached": False}
    idx = 0
    while idx < len(args):
        if args[idx] == "-d":
            result["detached"] = True
            idx += 1
        elif args[idx] == "-n" and idx + 1 < len(args):
            result["name"] = args[idx + 1]
            idx += 2
        elif args[idx] == "-t" and idx + 1 < len(args):
            result["target"] = args[idx + 1]
            idx += 2
        elif args[idx].startswith("-t"):
            result["target"] = args[idx][2:]
            idx += 1
        elif args[idx] == "-c" and idx + 1 < len(args):
            result["directory"] = args[idx + 1]
            idx += 2
        elif args[idx] == "-F" and idx + 1 < len(args):
            result["format"] = args[idx + 1]
            idx += 2
        elif args[idx] == "-P":
            result["print_info"] = True
            idx += 1
        else:
            result["command"] = args[idx:]
            break
    return result


def parse_select_pane(args: list[str]) -> dict:
    """Parse: select-pane [-t <target>]"""
    result = {}
    idx = 0
    while idx < len(args):
        if args[idx] == "-t" and idx + 1 < len(args):
            result["target"] = args[idx + 1]
            idx += 2
        elif args[idx].startswith("-t"):
            result["target"] = args[idx][2:]
            idx += 1
        else:
            idx += 1
    return result


def parse_set_environment(args: list[str]) -> dict:
    """Parse: set-environment [-g] [-t <target>] <name>=<value> or <name> <value>"""
    result = {}
    idx = 0
    while idx < len(args):
        if args[idx] == "-g":
            result["global"] = True
            idx += 1
        elif args[idx] == "-u":
            result["unset"] = True
            idx += 1
        elif args[idx] == "-t" and idx + 1 < len(args):
            result["target"] = args[idx + 1]
            idx += 2
        elif "=" in args[idx]:
            name, _, value = args[idx].partition("=")
            result["name"] = name
            result["value"] = value
            idx += 1
        else:
            if "name" not in result:
                result["name"] = args[idx]
            else:
                result["value"] = args[idx]
            idx += 1
    return result


def parse_wait_for(args: list[str]) -> dict:
    """Parse: wait-for [-S|-L|-U] <channel>"""
    result = {}
    idx = 0
    while idx < len(args):
        if args[idx] == "-S":
            result["signal"] = True
            idx += 1
        elif args[idx] == "-L":
            result["lock"] = True
            idx += 1
        elif args[idx] == "-U":
            result["unlock"] = True
            idx += 1
        else:
            result["channel"] = args[idx]
            idx += 1
    return result


def parse_resize_pane(args: list[str]) -> dict:
    """Parse: resize-pane [-t <target>] [-x <width>] [-y <height>] [-UDLR <amount>]"""
    result = {}
    idx = 0
    while idx < len(args):
        if args[idx] == "-t" and idx + 1 < len(args):
            result["target"] = args[idx + 1]
            idx += 2
        elif args[idx].startswith("-t"):
            result["target"] = args[idx][2:]
            idx += 1
        elif args[idx] == "-x" and idx + 1 < len(args):
            result["width"] = int(args[idx + 1])
            idx += 2
        elif args[idx] == "-y" and idx + 1 < len(args):
            result["height"] = int(args[idx + 1])
            idx += 2
        elif args[idx] in ("-U", "-D", "-L", "-R"):
            result["direction"] = args[idx][1:]
            if idx + 1 < len(args) and args[idx + 1].isdigit():
                result["amount"] = int(args[idx + 1])
                idx += 2
            else:
                result["amount"] = 1
                idx += 1
        else:
            idx += 1
    return result


COMMAND_PARSERS = {
    "has-session": parse_has_session,
    "new-session": parse_new_session,
    "split-window": parse_split_window,
    "send-keys": parse_send_keys,
    "kill-pane": parse_kill_pane,
    "list-panes": parse_list_panes,
    "list-windows": parse_list_windows,
    "display-message": parse_display_message,
    "attach-session": parse_attach_session,
    "list-sessions": parse_list_sessions,
    "new-window": parse_new_window,
    "select-pane": parse_select_pane,
    "set-environment": parse_set_environment,
    "set-env": parse_set_environment,
    "wait-for": parse_wait_for,
    "resize-pane": parse_resize_pane,
}


def send_to_conductor(request: dict) -> dict:
    """Send a JSON request to Conductor via named pipe, return the response.

    Retries briefly if the pipe is busy (server processing another request).
    """
    import time
    import win32file

    max_retries = 10
    retry_delay = 0.15  # seconds

    for attempt in range(max_retries):
        try:
            handle = win32file.CreateFile(
                PIPE_NAME,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,  # no sharing
                None,  # default security
                win32file.OPEN_EXISTING,
                0,  # default attributes
                None,  # no template
            )
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            # Final attempt failed
            print("error: Claude Conductor is not running", file=sys.stderr)
            sys.exit(1)

        try:
            data = json.dumps(request).encode("utf-8")
            win32file.WriteFile(handle, data)

            # Read response
            _, response_data = win32file.ReadFile(handle, 65536)
            return json.loads(response_data.decode("utf-8"))
        finally:
            win32file.CloseHandle(handle)

    # Should not reach here
    print("error: Claude Conductor is not running", file=sys.stderr)
    sys.exit(1)


def _debug_log(msg: str):
    """Append to debug log file (silent on failure)."""
    try:
        import os
        import datetime
        # Use a fixed path that works even from PyInstaller exe
        log_path = os.path.join(os.environ.get("SystemDrive", "C:"), "tmp", "shim_debug.log")
        with open(log_path, "a", encoding="utf-8") as f:
            ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def main():
    request = parse_args(sys.argv)
    _debug_log(f"argv={sys.argv[1:]}  parsed={request}")

    # Handle version locally (no pipe needed)
    if request["cmd"] == "version":
        print(FAKE_VERSION)
        _debug_log("  -> version (local)")
        sys.exit(0)

    # Everything else goes to Conductor
    response = send_to_conductor(request)
    _debug_log(f"  -> response={response}")

    stdout = response.get("stdout", "")
    stderr = response.get("stderr", "")
    exit_code = response.get("exit_code", 0)

    if stdout:
        print(stdout, end="")
        # Add trailing newline if stdout doesn't end with one
        if not stdout.endswith("\n"):
            print()
    if stderr:
        print(stderr, file=sys.stderr)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
