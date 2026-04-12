"""Track Claude thinking time per project per day.

Projects are identified by working directory (resolved via process tree),
so renames don't split accumulated time.
"""

import json
import os
import time
from datetime import date

import psutil

from .utils import has_spinner

_DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'claude_time.json')
_SAVE_INTERVAL = 30  # seconds between disk writes


def _resolve_project_dir(pid):
    """Walk child processes of a terminal PID to find Claude's working directory."""
    try:
        proc = psutil.Process(pid)
        for child in proc.children(recursive=True):
            try:
                if child.name().lower() == 'node.exe':
                    cwd = child.cwd()
                    if 'system32' not in cwd.lower():
                        return os.path.basename(cwd)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        pass
    return None


class TimeTracker:
    def __init__(self):
        self._prev_state = {}       # hwnd -> 'spinning' | 'idle' | 'choice'
        self._project_cache = {}    # hwnd -> project_name
        self._data = {}             # "project|YYYY-MM-DD" -> seconds
        self._dirty = False
        self._last_save = 0.0
        self._load()

    def _load(self):
        try:
            with open(_DATA_FILE, 'r') as f:
                self._data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}

    def _save(self):
        now = time.monotonic()
        if not self._dirty or now - self._last_save < _SAVE_INTERVAL:
            return
        try:
            with open(_DATA_FILE, 'w') as f:
                json.dump(self._data, f, indent=2)
            self._dirty = False
            self._last_save = now
        except OSError:
            pass

    def _get_project(self, hwnd, pid, display_title):
        """Resolve project name for a window, cached by hwnd."""
        if hwnd in self._project_cache:
            return self._project_cache[hwnd]
        project = _resolve_project_dir(pid) or display_title
        self._project_cache[hwnd] = project
        return project

    def update(self, windows, refresh_secs=2):
        """Called each refresh cycle. Detects state transitions and accumulates time."""
        today = date.today().isoformat()

        for w in windows:
            project = self._get_project(w.hwnd, w.pid, w.display_title or w.title)
            key = f"{project}|{today}"

            # Determine current state from ground truth (title spinner chars)
            if has_spinner(w.title):
                current = 'spinning'
            elif w.attention_type == 'choice':
                current = 'choice'
            else:
                current = 'idle'

            prev = self._prev_state.get(w.hwnd)

            # Accumulate spinning time
            if current == 'spinning':
                self._data[key] = self._data.get(key, 0) + refresh_secs
                self._dirty = True

            # Transition bonuses (only on actual transitions from spinning)
            if prev == 'spinning':
                if current == 'idle':
                    self._data[key] = self._data.get(key, 0) + 300  # +5 min
                    self._dirty = True
                elif current == 'choice':
                    self._data[key] = self._data.get(key, 0) + 60   # +1 min
                    self._dirty = True

            self._prev_state[w.hwnd] = current

        # Clean up closed windows
        live = {w.hwnd for w in windows}
        self._prev_state = {h: s for h, s in self._prev_state.items() if h in live}
        self._project_cache = {h: p for h, p in self._project_cache.items() if h in live}

        self._save()

    def get_today_seconds(self, hwnd):
        """Get accumulated seconds for a window's project today."""
        project = self._project_cache.get(hwnd)
        if not project:
            return 0
        key = f"{project}|{date.today().isoformat()}"
        return self._data.get(key, 0)

    def force_save(self):
        """Save immediately (e.g. on shutdown)."""
        if self._dirty:
            try:
                with open(_DATA_FILE, 'w') as f:
                    json.dump(self._data, f, indent=2)
                self._dirty = False
            except OSError:
                pass
