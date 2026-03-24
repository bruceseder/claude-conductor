import tkinter as tk

from . import config as cfg
from .window_manager import WindowManager
from .monitor_manager import MonitorManager
from .tiling import calculate_layout, distribute_across_monitors
from .ui import PowerWidget


class App:
    def __init__(self):
        self._root = tk.Tk()
        self._root.withdraw()  # Hidden root

        self._monitor_mgr = MonitorManager()
        self._window_mgr = WindowManager()
        self._selected_monitor = "All"
        self._monitor_refresh_counter = 0

        self._widget = PowerWidget(
            master=self._root,
            monitors=self._monitor_mgr.monitors,
            on_focus=self._on_focus,
            on_tile=self._on_tile,
            on_minimize_all=self._on_minimize_all,
            on_restore_all=self._on_restore_all,
            on_refresh=self._refresh,
            on_monitor_change=self._on_monitor_change,
        )

        # Exclude our own window from enumeration
        self._root.after(200, self._exclude_self)

        # Setup keyboard shortcuts
        self._widget.setup_keybindings(lambda: self._window_mgr.windows)

        # Start refresh loop
        self._root.after(500, self._refresh)

    def _exclude_self(self):
        try:
            hwnd = self._widget.get_hwnd()
            if hwnd:
                self._window_mgr.add_exclude(hwnd)
        except Exception:
            pass

    def _refresh(self):
        """Enumerate windows and update the UI."""
        try:
            # Refresh monitors every ~30 seconds (15 cycles), not every 2s
            self._monitor_refresh_counter += 1
            if self._monitor_refresh_counter >= 15:
                self._monitor_refresh_counter = 0
                self._monitor_mgr.refresh()
                self._widget.update_monitors(self._monitor_mgr.monitors)

            windows = self._window_mgr.enumerate_windows()
            self._widget.update_window_list(windows)
        except Exception:
            pass
        self._root.after(cfg.REFRESH_INTERVAL_MS, self._refresh)

    def _on_focus(self, hwnd):
        self._window_mgr.focus_window(hwnd)

    def _on_tile(self, mode):
        windows = self._window_mgr.windows
        if not windows:
            return

        if self._selected_monitor == "Distribute":
            areas = [m.work_area for m in self._monitor_mgr.monitors]
            positions = distribute_across_monitors(windows, areas, mode)
        elif self._selected_monitor == "All":
            area = self._monitor_mgr.get_combined_work_area()
            positions = calculate_layout(mode, windows, area)
        else:
            # Specific monitor
            for m in self._monitor_mgr.monitors:
                if m.name == self._selected_monitor:
                    area = m.work_area
                    break
            else:
                area = self._monitor_mgr.get_work_area(0)
            positions = calculate_layout(mode, windows, area)

        for hwnd, x, y, w, h in positions:
            self._window_mgr.move_and_resize(hwnd, x, y, w, h)

        # Refresh after tiling
        self._root.after(300, self._refresh)

    def _on_minimize_all(self):
        self._window_mgr.minimize_all()
        self._root.after(300, self._refresh)

    def _on_restore_all(self):
        self._window_mgr.restore_all()
        self._root.after(300, self._refresh)

    def _on_monitor_change(self, value):
        self._selected_monitor = value

    def run(self):
        self._root.mainloop()
