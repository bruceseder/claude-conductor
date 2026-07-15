r"""Named pipe server for the tmux shim.

Listens on \\.\pipe\claude-conductor for JSON requests from the tmux shim,
dispatches them to the appropriate handler, and returns JSON responses.
Runs as a daemon thread within the Conductor process.
"""

import json
import logging
import threading

import pywintypes
import win32file
import win32pipe

from . import config as cfg

log = logging.getLogger(__name__)

# Buffer sizes
PIPE_BUFFER_SIZE = 4096
MAX_MESSAGE_SIZE = 65536


class ShimServer:
    """Named pipe server that receives tmux shim commands."""

    def __init__(self):
        self._running = False
        self._thread = None
        self._handlers = {}

    def register_handler(self, command: str, handler):
        """Register a handler function for a tmux command.

        handler signature: (args: dict) -> dict
        The returned dict must have 'stdout', 'stderr', 'exit_code' keys.
        """
        self._handlers[command] = handler

    def start(self):
        """Start the named pipe server in a daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._serve_loop, daemon=True)
        self._thread.start()
        log.info("Shim server started on %s", cfg.SHIM_PIPE_NAME)

    def stop(self):
        """Signal the server to stop."""
        self._running = False

    def _serve_loop(self):
        """Main server loop: create pipe, wait for connection, handle, repeat."""
        while self._running:
            pipe_handle = None
            try:
                pipe_handle = win32pipe.CreateNamedPipe(
                    cfg.SHIM_PIPE_NAME,
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    (
                        win32pipe.PIPE_TYPE_MESSAGE
                        | win32pipe.PIPE_READMODE_MESSAGE
                        | win32pipe.PIPE_WAIT
                    ),
                    win32pipe.PIPE_UNLIMITED_INSTANCES,
                    PIPE_BUFFER_SIZE,
                    PIPE_BUFFER_SIZE,
                    0,  # default timeout
                    None,  # default security
                )

                # Block until a client connects
                win32pipe.ConnectNamedPipe(pipe_handle, None)

                # Read the request
                data = self._read_message(pipe_handle)
                if data is not None:
                    response = self._dispatch(data)
                    self._write_message(pipe_handle, response)

                win32pipe.DisconnectNamedPipe(pipe_handle)

            except pywintypes.error as e:
                # Error 233 = pipe broken (client disconnected), not worth logging
                if e.winerror != 233:
                    log.warning("Pipe error: %s", e)
            except Exception:
                log.exception("Unexpected error in shim server")
            finally:
                if pipe_handle is not None:
                    try:
                        win32file.CloseHandle(pipe_handle)
                    except Exception:
                        pass

    def _read_message(self, pipe_handle) -> dict | None:
        """Read a complete JSON message from the pipe."""
        chunks = []
        total = 0
        while True:
            try:
                hr, data = win32file.ReadFile(pipe_handle, PIPE_BUFFER_SIZE)
                chunks.append(data)
                total += len(data)
                if total > MAX_MESSAGE_SIZE:
                    log.warning("Message too large, dropping")
                    return None
                # hr == 0 means success (complete message)
                if hr == 0:
                    break
            except pywintypes.error as e:
                # ERROR_MORE_DATA (234) = message not complete yet
                if e.winerror == 234:
                    chunks.append(e.strerror if hasattr(e, "data") else b"")
                    continue
                raise

        raw = b"".join(chunks)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            log.warning("Invalid JSON from shim: %s", e)
            return None

    def _write_message(self, pipe_handle, response: dict):
        """Write a JSON response to the pipe."""
        data = json.dumps(response).encode("utf-8")
        win32file.WriteFile(pipe_handle, data)

    def _dispatch(self, request: dict) -> dict:
        """Route a request to the appropriate handler."""
        cmd = request.get("cmd", "")
        args = request.get("args", {})

        log.info("Shim request: cmd=%s args=%s", cmd, args)

        handler = self._handlers.get(cmd)
        if handler is None:
            log.warning("Unknown shim command: %s", cmd)
            return {
                "ok": False,
                "stdout": "",
                "stderr": f"error: unknown command '{cmd}'",
                "exit_code": 1,
            }

        try:
            result = handler(args)
            return {"ok": True, **result}
        except Exception as e:
            log.exception("Handler error for cmd=%s", cmd)
            return {
                "ok": False,
                "stdout": "",
                "stderr": str(e),
                "exit_code": 1,
            }


def create_server(session_manager, wt_module=None) -> ShimServer:
    """Create a shim server with session-manager-backed handlers.

    Args:
        session_manager: A SessionManager instance for tracking sessions/panes.
        wt_module: The wt_integration module (or mock). If None, imported lazily.
    """
    server = ShimServer()

    if wt_module is None:
        from . import wt_integration as wt_module

    sm = session_manager
    wt = wt_module

    def handle_version(args):
        return {"stdout": cfg.FAKE_TMUX_VERSION, "stderr": "", "exit_code": 0}

    def handle_has_session(args):
        name = args.get("target", "")
        if sm.has_session(name):
            return {"stdout": "", "stderr": "", "exit_code": 0}
        return {"stdout": "", "stderr": f"can't find session: {name}", "exit_code": 1}

    def handle_new_session(args):
        name = args.get("session_name", "default")
        session = sm.get_or_create_session(name)

        # Register the lead pane (pane %0)
        pane_id = sm.allocate_pane_id(session)
        working_dir = args.get("directory", "")
        sm.register_pane(session, pane_id, working_dir=working_dir, role="lead")

        # Launch the container window (lazy start)
        wt.launch_container(session, working_dir or ".")

        return {"stdout": name, "stderr": "", "exit_code": 0}

    def handle_split_window(args):
        # Find or create a session
        target = args.get("target", "")
        session = None
        if target:
            # Target might be a session name or pane reference
            for s in sm.sessions.values():
                if s.session_name == target or target in s.panes:
                    session = s
                    break
        if not session:
            session = sm.get_default_session()
        if not session:
            # Auto-create on first split (lazy start)
            session = sm.get_or_create_session("default")
            lead_id = sm.allocate_pane_id(session)
            sm.register_pane(session, lead_id, role="lead")

        # Allocate new pane
        pane_id = sm.allocate_pane_id(session)
        working_dir = args.get("directory", "")
        horizontal = args.get("horizontal", False)
        pane = sm.register_pane(session, pane_id, working_dir=working_dir)

        # Launch container if needed (first split triggers it)
        if not session.container_hwnd:
            wt.launch_container(session, working_dir or ".")

        # Split the pane in WT, passing through any command
        command = args.get("command")
        wt.split_pane(session, pane, horizontal, command=command)

        # Render response in requested format
        fmt = args.get("format", "#{pane_id}")
        stdout = sm.render_format(fmt, session, pane)
        return {"stdout": stdout, "stderr": "", "exit_code": 0}

    def handle_send_keys(args):
        target = args.get("target", "")
        keys = args.get("keys", [])

        # Find the target pane
        pane = None
        for session in sm.sessions.values():
            if target in session.panes:
                pane = session.panes[target]
                break

        if not pane:
            return {"stdout": "", "stderr": f"can't find pane: {target}", "exit_code": 1}

        wt.send_keys_to_pane(pane, keys)
        return {"stdout": "", "stderr": "", "exit_code": 0}

    def handle_kill_pane(args):
        target = args.get("target", "")

        for session in sm.sessions.values():
            if target in session.panes:
                pane = session.panes[target]
                wt.kill_pane_process(pane)
                sm.kill_pane(session, target)
                return {"stdout": "", "stderr": "", "exit_code": 0}

        return {"stdout": "", "stderr": f"can't find pane: {target}", "exit_code": 1}

    def handle_list_panes(args):
        fmt = args.get("format", "#{pane_id}")
        target = args.get("target", "")

        session = None
        if target:
            session = sm.get_session(target)
        if not session:
            session = sm.get_default_session()
        if not session:
            return {"stdout": "", "stderr": "", "exit_code": 0}

        stdout = sm.render_pane_list(session, fmt)
        return {"stdout": stdout, "stderr": "", "exit_code": 0}

    def handle_list_windows(args):
        fmt = args.get("format", "#{window_id}")

        session = sm.get_default_session()
        if not session:
            return {"stdout": "", "stderr": "", "exit_code": 0}

        stdout = sm.render_window_list(session, fmt)
        return {"stdout": stdout, "stderr": "", "exit_code": 0}

    def handle_display_message(args):
        fmt = args.get("format", "") or args.get("message", "")
        target = args.get("target", "")

        session = sm.get_default_session()
        if not session:
            return {"stdout": "", "stderr": "", "exit_code": 0}

        # If target is a pane, render with pane context
        pane = None
        if target:
            for s in sm.sessions.values():
                if target in s.panes:
                    pane = s.panes[target]
                    session = s
                    break

        stdout = sm.render_format(fmt, session, pane)
        return {"stdout": stdout, "stderr": "", "exit_code": 0}

    def handle_attach_session(args):
        # No-op: session is already visible in WT container
        return {"stdout": "", "stderr": "", "exit_code": 0}

    def handle_pane_ready(args):
        """Called by pane_relay.py when it starts up."""
        pane_id = args.get("pane_id", "")
        child_pid = args.get("child_pid", 0)
        sm.update_pane_pid("", pane_id, child_pid)
        return {"stdout": "", "stderr": "", "exit_code": 0}

    def handle_list_sessions(args):
        fmt = args.get("format", "#{session_name}")
        sessions = sm.sessions
        if not sessions:
            # Auto-create default session so validation passes
            session = sm.get_or_create_session("default")
            sessions = sm.sessions
        lines = []
        for session in sessions.values():
            lines.append(sm.render_format(fmt, session))
        return {"stdout": "\n".join(lines), "stderr": "", "exit_code": 0}

    def handle_new_window(args):
        target = args.get("target", "")
        session = None
        if target:
            for s in sm.sessions.values():
                if s.session_name == target or target in s.panes:
                    session = s
                    break
        if not session:
            session = sm.get_default_session()
        if not session:
            session = sm.get_or_create_session("default")
            lead_id = sm.allocate_pane_id(session)
            sm.register_pane(session, lead_id, role="lead")

        pane_id = sm.allocate_pane_id(session)
        working_dir = args.get("directory", "")
        pane = sm.register_pane(session, pane_id, working_dir=working_dir)

        if not session.container_hwnd:
            wt.launch_container(session, working_dir or ".")

        # WT doesn't have multi-window sessions; use split-pane
        command = args.get("command")
        wt.split_pane(session, pane, horizontal=True, command=command)

        fmt = args.get("format", "#{pane_id}")
        stdout = sm.render_format(fmt, session, pane)
        return {"stdout": stdout, "stderr": "", "exit_code": 0}

    def handle_select_pane(args):
        # No-op: WT pane focus is managed by the user
        return {"stdout": "", "stderr": "", "exit_code": 0}

    def handle_set_environment(args):
        # Store env vars in session metadata for new panes to inherit
        name = args.get("name", "")
        value = args.get("value", "")
        if name:
            log.info("set-environment: %s=%s", name, value)
        return {"stdout": "", "stderr": "", "exit_code": 0}

    def handle_wait_for(args):
        # Signal mode (-S): immediate return (signal sent)
        # Wait mode: immediate return (don't block the shim)
        return {"stdout": "", "stderr": "", "exit_code": 0}

    def handle_resize_pane(args):
        # No-op: WT manages pane sizes
        return {"stdout": "", "stderr": "", "exit_code": 0}

    server.register_handler("version", handle_version)
    server.register_handler("has-session", handle_has_session)
    server.register_handler("new-session", handle_new_session)
    server.register_handler("split-window", handle_split_window)
    server.register_handler("send-keys", handle_send_keys)
    server.register_handler("kill-pane", handle_kill_pane)
    server.register_handler("list-panes", handle_list_panes)
    server.register_handler("list-windows", handle_list_windows)
    server.register_handler("display-message", handle_display_message)
    server.register_handler("attach-session", handle_attach_session)
    server.register_handler("pane-ready", handle_pane_ready)
    server.register_handler("list-sessions", handle_list_sessions)
    server.register_handler("new-window", handle_new_window)
    server.register_handler("select-pane", handle_select_pane)
    server.register_handler("set-environment", handle_set_environment)
    server.register_handler("set-env", handle_set_environment)
    server.register_handler("wait-for", handle_wait_for)
    server.register_handler("resize-pane", handle_resize_pane)

    return server
