"""Agent Teams session lifecycle manager.

Tracks team sessions, panes, and their metadata. Provides format string
rendering for tmux-compatible responses and coordinates with WTIntegration
for actual pane operations.
"""

import logging
import os
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class PaneInfo:
    pane_id: str           # "%0", "%1", etc.
    process_pid: int = 0   # PID of shell/relay process in pane
    working_dir: str = ""  # Starting directory
    role: str = "teammate" # "lead" or "teammate"
    agent_id: str = ""     # Claude Code's agent ID
    agent_name: str = ""   # Claude Code's agent name
    hwnd: int = 0          # TermControl HWND (for UI Automation)
    created_at: float = field(default_factory=time.time)
    alive: bool = True


@dataclass
class TeamSession:
    session_name: str
    window_name: str             # WT named window identifier
    container_hwnd: int = 0      # The Windows Terminal window HWND
    panes: dict = field(default_factory=dict)  # pane_id -> PaneInfo
    next_pane_id: int = 0        # Counter for generating %0, %1, %2...
    lead_pane_id: str = ""       # The pane ID of the lead agent
    created_at: float = field(default_factory=time.time)


class SessionManager:
    """Manages agent team sessions and pane lifecycle."""

    def __init__(self):
        self._sessions: dict[str, TeamSession] = {}

    def has_session(self, name: str) -> bool:
        return name in self._sessions

    def get_session(self, name: str) -> TeamSession | None:
        return self._sessions.get(name)

    def get_default_session(self) -> TeamSession | None:
        """Get the first (or only) active session."""
        if self._sessions:
            return next(iter(self._sessions.values()))
        return None

    def create_session(self, name: str) -> TeamSession:
        """Create a new team session."""
        window_name = f"claude-team-{name}"
        session = TeamSession(
            session_name=name,
            window_name=window_name,
        )
        self._sessions[name] = session
        log.info("Created session '%s' (window: %s)", name, window_name)
        return session

    def get_or_create_session(self, name: str = "default") -> TeamSession:
        """Get existing session or create a new one."""
        if name in self._sessions:
            return self._sessions[name]
        return self.create_session(name)

    def allocate_pane_id(self, session: TeamSession) -> str:
        """Allocate the next pane ID for a session."""
        pane_id = f"%{session.next_pane_id}"
        session.next_pane_id += 1
        return pane_id

    def register_pane(self, session: TeamSession, pane_id: str,
                      working_dir: str = "", role: str = "teammate") -> PaneInfo:
        """Register a new pane in the session."""
        pane = PaneInfo(
            pane_id=pane_id,
            working_dir=working_dir or os.getcwd(),
            role=role,
        )
        session.panes[pane_id] = pane
        if role == "lead":
            session.lead_pane_id = pane_id
        log.info("Registered pane %s (role=%s) in session '%s'",
                 pane_id, role, session.session_name)
        return pane

    def update_pane_pid(self, session_name: str, pane_id: str, pid: int):
        """Update a pane's child process PID (called when relay reports in)."""
        session = self._sessions.get(session_name)
        if not session:
            # Try to find the pane in any session
            for s in self._sessions.values():
                if pane_id in s.panes:
                    session = s
                    break
        if session and pane_id in session.panes:
            session.panes[pane_id].process_pid = pid
            log.info("Pane %s PID updated to %d", pane_id, pid)

    def kill_pane(self, session: TeamSession, pane_id: str) -> bool:
        """Mark a pane as dead and remove it from the session."""
        pane = session.panes.get(pane_id)
        if not pane:
            return False
        pane.alive = False
        del session.panes[pane_id]
        log.info("Killed pane %s in session '%s'", pane_id, session.session_name)

        # If all panes gone, clean up session
        if not session.panes:
            log.info("Session '%s' has no panes left, cleaning up", session.session_name)
            del self._sessions[session.session_name]
        return True

    def list_panes(self, session: TeamSession) -> list[PaneInfo]:
        """List all alive panes in a session."""
        return [p for p in session.panes.values() if p.alive]

    def render_format(self, fmt: str, session: TeamSession,
                      pane: PaneInfo | None = None) -> str:
        """Render a tmux format string with session/pane variables.

        Supports: #{pane_id}, #{pane_pid}, #{pane_current_path},
                  #{window_id}, #{session_name}, #{window_index},
                  #{pane_index}, #{pane_active}
        """
        result = fmt
        result = result.replace("#{session_name}", session.session_name)
        result = result.replace("#{window_id}", "@0")
        result = result.replace("#{window_index}", "0")

        if pane:
            result = result.replace("#{pane_id}", pane.pane_id)
            result = result.replace("#{pane_pid}", str(pane.process_pid))
            result = result.replace("#{pane_current_path}", pane.working_dir)
            # pane_index = numeric part of pane_id
            idx = pane.pane_id.lstrip("%")
            result = result.replace("#{pane_index}", idx)
            is_active = "1" if pane.pane_id == session.lead_pane_id else "0"
            result = result.replace("#{pane_active}", is_active)

        return result

    def render_pane_list(self, session: TeamSession, fmt: str) -> str:
        """Render format string for each pane, one per line."""
        lines = []
        for pane in self.list_panes(session):
            lines.append(self.render_format(fmt, session, pane))
        return "\n".join(lines)

    def render_window_list(self, session: TeamSession, fmt: str) -> str:
        """Render format string for each window (we only have one)."""
        return self.render_format(fmt, session)

    @property
    def sessions(self) -> dict[str, TeamSession]:
        return dict(self._sessions)

    @property
    def container_hwnds(self) -> set[int]:
        """Return the set of all managed container window HWNDs."""
        return {s.container_hwnd for s in self._sessions.values() if s.container_hwnd}
