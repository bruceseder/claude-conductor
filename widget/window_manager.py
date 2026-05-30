import time
from dataclasses import dataclass
import win32gui
import win32con
import win32process
import pywintypes

from .config import WINDOW_CLASSES, TITLE_KEYWORDS, TITLE_EXCLUDE, CLASS_EXCLUDE
from .utils import clean_title, is_claude_window, has_spinner, force_set_foreground
from .terminal_reader import detect_attention_type


# Cache UIA detections for stable states. Idle/choice rarely flip without a
# title change (the braille spinner cycles in the title during work, and that
# flip already busts the cache). Working/None are NEVER cached, so the
# spinnerless-tool-call -> idle transition is always picked up on the next tick.
_UIA_CACHE_TTL_SECS = 4.0
_UIA_CACHED_STATES = ('idle', 'choice')

# Cap UIA reads per refresh tick so 4+ windows expiring on the same tick don't
# all cross-process IPC at once. Excess windows reuse their stale cached value
# for one extra tick (worst case ~2s of detection lag).
_UIA_MAX_READS_PER_TICK = 2


@dataclass
class TrackedWindow:
    hwnd: int
    title: str
    display_title: str
    is_claude: bool
    class_name: str
    pid: int
    is_minimized: bool
    needs_attention: bool = False
    attention_type: str = ''  # 'choice', 'idle', or ''


class WindowManager:
    def __init__(self, exclude_hwnds=None):
        self._exclude_hwnds = set(exclude_hwnds or [])
        self._windows = []
        # hwnd -> 'choice' or 'idle'; presence means needs attention
        self._attention_state = {}
        self._nicknamed_hwnds = set()  # hwnds with user-assigned nicknames
        self._known_claude_hwnds = set()  # hwnds ever identified as Claude (persists until closed)
        self._atype_cache = {}  # hwnd -> (atype, title, monotonic_ts) for cached stable states

    def add_exclude(self, hwnd):
        self._exclude_hwnds.add(hwnd)

    def set_nicknamed_hwnds(self, hwnds):
        self._nicknamed_hwnds = set(hwnds)

    def _detect_attention_cached(self, hwnd, title, now):
        """Wrap detect_attention_type with a per-hwnd, title-keyed TTL cache.

        Also enforces a per-tick UIA budget (_uia_reads_remaining is reset at
        the top of enumerate_windows). When the budget is exhausted, fall back
        to the stale cached value rather than doing another cross-process read.
        """
        cached = self._atype_cache.get(hwnd)
        if cached is not None:
            cached_atype, cached_title, cached_ts = cached
            if cached_title == title and (now - cached_ts) < _UIA_CACHE_TTL_SECS:
                return cached_atype

        # Budget exhausted: defer this read by one tick. If we have any cached
        # value (even stale or with old title), prefer it over None to avoid a
        # one-tick attention flicker.
        if self._uia_reads_remaining <= 0:
            if cached is not None:
                return cached[0]
            return None

        self._uia_reads_remaining -= 1
        atype = detect_attention_type(hwnd)
        if atype in _UIA_CACHED_STATES:
            self._atype_cache[hwnd] = (atype, title, now)
        else:
            self._atype_cache.pop(hwnd, None)
        return atype

    def enumerate_windows(self):
        """Find all terminal/Claude windows."""
        results = []
        now = time.monotonic()
        self._uia_reads_remaining = _UIA_MAX_READS_PER_TICK

        def callback(hwnd, _):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                if hwnd in self._exclude_hwnds:
                    return True

                title = win32gui.GetWindowText(hwnd)
                if not title:
                    return True

                title_lower = title.lower()

                # Check exclusions
                for exc in TITLE_EXCLUDE:
                    if exc.lower() in title_lower:
                        return True

                class_name = win32gui.GetClassName(hwnd)

                # Skip browsers and other excluded window classes
                if class_name in CLASS_EXCLUDE:
                    return True

                # Match by class name or title keywords
                matched = class_name in WINDOW_CLASSES
                if not matched:
                    matched = any(kw in title_lower for kw in TITLE_KEYWORDS)

                if not matched:
                    return True

                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                is_minimized = bool(win32gui.IsIconic(hwnd))
                is_claude = is_claude_window(title) or hwnd in self._nicknamed_hwnds or hwnd in self._known_claude_hwnds
                if not is_claude:
                    return True  # Skip non-Claude windows

                self._known_claude_hwnds.add(hwnd)
                currently_spinning = has_spinner(title)

                if currently_spinning:
                    self._attention_state.pop(hwnd, None)
                else:
                    # Re-detect attention type each cycle so state
                    # transitions (idle → choice → working) are caught
                    if class_name == 'CASCADIA_HOSTING_WINDOW_CLASS':
                        atype = self._detect_attention_cached(hwnd, title, now)
                    else:
                        atype = None
                    if atype == 'working':
                        # "esc to interrupt" footer detected — Claude is processing,
                        # treat as spinner-positive even when title lacks braille.
                        self._attention_state.pop(hwnd, None)
                    else:
                        self._attention_state[hwnd] = atype or 'idle'

                in_attention = hwnd in self._attention_state

                results.append(TrackedWindow(
                    hwnd=hwnd,
                    title=title,
                    display_title=clean_title(title),
                    is_claude=True,
                    class_name=class_name,
                    pid=pid,
                    is_minimized=is_minimized,
                    needs_attention=in_attention,
                    attention_type=self._attention_state.get(hwnd, ''),
                ))
            except Exception:
                pass
            return True

        win32gui.EnumWindows(callback, None)

        # Clean up stale hwnds
        live_hwnds = {w.hwnd for w in results}
        self._attention_state = {h: v for h, v in self._attention_state.items() if h in live_hwnds}
        self._known_claude_hwnds &= live_hwnds
        self._atype_cache = {h: v for h, v in self._atype_cache.items() if h in live_hwnds}

        # Sort: attention first, then alphabetically
        results.sort(key=lambda w: (not w.needs_attention, w.display_title.lower()))
        self._windows = results
        return results

    @property
    def windows(self):
        return list(self._windows)

    def focus_window(self, hwnd):
        self.clear_attention(hwnd)
        force_set_foreground(hwnd)

    def clear_attention(self, hwnd):
        """Clear attention state when the user focuses a window."""
        self._attention_state.pop(hwnd, None)

    def minimize_window(self, hwnd):
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        except pywintypes.error:
            pass

    def restore_window(self, hwnd):
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        except pywintypes.error:
            pass

    def move_and_resize(self, hwnd, x, y, width, height):
        try:
            placement = win32gui.GetWindowPlacement(hwnd)
            if placement[1] == win32con.SW_SHOWMAXIMIZED:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

            win32gui.SetWindowPos(
                hwnd, win32con.HWND_TOP,
                int(x), int(y), int(width), int(height),
                win32con.SWP_SHOWWINDOW
            )
        except pywintypes.error:
            pass

    def minimize_all(self):
        for w in self._windows:
            self.minimize_window(w.hwnd)

    def restore_all(self):
        for w in self._windows:
            self.restore_window(w.hwnd)
