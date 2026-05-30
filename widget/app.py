import gc
import tkinter as tk

from . import config as cfg
from .window_manager import WindowManager
from .monitor_manager import MonitorManager
from .tiling import calculate_layout, distribute_across_monitors
from .time_tracker import TimeTracker
from .terminal_reader import release_uia
from .ui import PowerWidget


class App:
    def __init__(self):
        self._root = tk.Tk()
        # Park the root offscreen at 1x1 before withdrawing, so any stray
        # remap (Windows DWM occasionally re-maps a withdrawn root when its
        # Toplevel children change attributes) won't flash at (0,0).
        self._root.geometry('1x1+-32000+-32000')
        self._root.withdraw()  # Hidden root

        self._monitor_mgr = MonitorManager()
        self._window_mgr = WindowManager()
        self._time_tracker = TimeTracker()
        self._selected_monitor = "All"
        self._monitor_refresh_counter = 0
        self._refresh_after_id = None
        self._shutting_down = False

        self._widget = PowerWidget(
            master=self._root,
            monitors=self._monitor_mgr.monitors,
            on_focus=self._on_focus,
            on_tile=self._on_tile,
            on_minimize_all=self._on_minimize_all,
            on_restore_all=self._on_restore_all,
            on_refresh=self._refresh,
            on_monitor_change=self._on_monitor_change,
            on_close=self.shutdown,
        )

        # Exclude our own window from enumeration
        self._root.after(200, self._exclude_self)

        # Setup keyboard shortcuts
        self._widget.setup_keybindings(lambda: self._window_mgr.windows)

        # Start refresh loop
        self._refresh_after_id = self._root.after(500, self._refresh)

    def _exclude_self(self):
        try:
            hwnd = self._widget.get_hwnd()
            if hwnd:
                self._window_mgr.add_exclude(hwnd)
        except Exception:
            pass

    def _refresh(self):
        """Enumerate windows and update the UI."""
        if self._shutting_down:
            return
        try:
            # Refresh monitors every ~30 seconds (15 cycles), not every 2s
            self._monitor_refresh_counter += 1
            if self._monitor_refresh_counter >= 15:
                self._monitor_refresh_counter = 0
                self._monitor_mgr.refresh()
                self._widget.update_monitors(self._monitor_mgr.monitors)

            self._window_mgr.set_nicknamed_hwnds(self._widget.get_nicknamed_hwnds())
            windows = self._window_mgr.enumerate_windows()
            self._time_tracker.update(windows, refresh_secs=cfg.REFRESH_INTERVAL_MS // 1000)
            self._widget.update_window_list(windows, self._time_tracker)
        except Exception:
            pass
        self._refresh_after_id = self._root.after(cfg.REFRESH_INTERVAL_MS, self._refresh)

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

    def shutdown(self):
        """Orderly teardown so closing doesn't freeze for ~20s after long
        sessions. The freeze comes from CoUninitialize releasing accumulated
        UIA proxy refs synchronously at interpreter exit; we drop those refs
        here while the Tk loop is still pumping messages.
        """
        if self._shutting_down:
            return
        self._shutting_down = True

        if self._refresh_after_id is not None:
            try:
                self._root.after_cancel(self._refresh_after_id)
            except Exception:
                pass
            self._refresh_after_id = None

        try:
            self._time_tracker.force_save()
        except Exception:
            pass

        try:
            self._widget.reset_all_borders()
        except Exception:
            pass

        # Drop the cached IUIAutomation reference, then run a GC pass so
        # COM proxies created by the UIA tree walks release now (Release()
        # IPCs to Windows Terminal happen here, not at process exit).
        try:
            release_uia()
        except Exception:
            pass
        gc.collect()

        try:
            self._root.destroy()
        except Exception:
            pass

    def run(self):
        self._root.mainloop()
