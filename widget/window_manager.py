from dataclasses import dataclass
import win32gui
import win32con
import win32process
import pywintypes

from .config import WINDOW_CLASSES, TITLE_KEYWORDS, TITLE_EXCLUDE
from .utils import clean_title, is_claude_window, has_spinner, force_set_foreground
from .terminal_reader import detect_attention_type


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

    def add_exclude(self, hwnd):
        self._exclude_hwnds.add(hwnd)

    def enumerate_windows(self):
        """Find all terminal/Claude windows."""
        results = []

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

                # Match by class name or title keywords
                matched = class_name in WINDOW_CLASSES
                if not matched:
                    matched = any(kw in title_lower for kw in TITLE_KEYWORDS)

                if not matched:
                    return True

                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                is_minimized = bool(win32gui.IsIconic(hwnd))
                is_claude = is_claude_window(title)
                currently_spinning = has_spinner(title)

                # A Claude window with no spinner = waiting for input
                # A Claude window with spinner = actively working
                if is_claude:
                    if currently_spinning:
                        self._attention_state.pop(hwnd, None)
                    elif hwnd not in self._attention_state or True:
                        # Re-detect attention type each cycle
                        if class_name == 'CASCADIA_HOSTING_WINDOW_CLASS':
                            atype = detect_attention_type(hwnd) or 'idle'
                        else:
                            atype = 'idle'
                        self._attention_state[hwnd] = atype

                in_attention = hwnd in self._attention_state

                results.append(TrackedWindow(
                    hwnd=hwnd,
                    title=title,
                    display_title=clean_title(title),
                    is_claude=is_claude,
                    class_name=class_name,
                    pid=pid,
                    is_minimized=is_minimized,
                    needs_attention=in_attention,
                    attention_type=self._attention_state.get(hwnd, ''),
                ))
            except pywintypes.error:
                pass
            return True

        win32gui.EnumWindows(callback, None)

        # Clean up stale hwnds
        live_hwnds = {w.hwnd for w in results}
        self._attention_state = {h: v for h, v in self._attention_state.items() if h in live_hwnds}

        # Sort: attention first, then Claude, then alphabetically
        results.sort(key=lambda w: (not w.needs_attention, not w.is_claude, w.display_title.lower()))
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
