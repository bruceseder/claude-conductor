from dataclasses import dataclass
import win32api
import win32gui


@dataclass
class MonitorInfo:
    index: int
    handle: int
    work_area: tuple  # (left, top, right, bottom) excludes taskbar
    full_rect: tuple
    is_primary: bool
    name: str
    width: int
    height: int


class MonitorManager:
    def __init__(self):
        self._monitors = []
        self._handle_to_index = {}
        self.refresh()

    def refresh(self):
        self._monitors = []
        self._handle_to_index = {}

        for i, (hmon, _hdc, _rect) in enumerate(win32api.EnumDisplayMonitors()):
            info = win32api.GetMonitorInfo(hmon)
            work = info['Work']
            full = info['Monitor']
            is_primary = bool(info.get('Flags', 0) & 1)

            self._monitors.append(MonitorInfo(
                index=i,
                handle=hmon,
                work_area=tuple(work),
                full_rect=tuple(full),
                is_primary=is_primary,
                name=f"Monitor {i + 1}{' (Primary)' if is_primary else ''}",
                width=work[2] - work[0],
                height=work[3] - work[1],
            ))
            self._handle_to_index[hmon] = i

    @property
    def monitors(self):
        return list(self._monitors)

    @property
    def count(self):
        return len(self._monitors)

    def get_work_area(self, index):
        """Get work area for a specific monitor."""
        if 0 <= index < len(self._monitors):
            return self._monitors[index].work_area
        return self._monitors[0].work_area

    def get_combined_work_area(self):
        """Bounding rectangle of all monitor work areas."""
        if not self._monitors:
            return (0, 0, 1920, 1080)
        left = min(m.work_area[0] for m in self._monitors)
        top = min(m.work_area[1] for m in self._monitors)
        right = max(m.work_area[2] for m in self._monitors)
        bottom = max(m.work_area[3] for m in self._monitors)
        return (left, top, right, bottom)

    def monitor_index_from_hwnd(self, hwnd):
        """Get monitor index for a window handle."""
        try:
            hmon = win32api.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
            return self._handle_to_index.get(hmon, 0)
        except Exception:
            return 0
