import os
import tkinter as tk

from . import config as cfg
from .utils import debug_log
from .window_manager import WindowManager
from .monitor_manager import MonitorManager
from .tiling import calculate_layout, distribute_across_monitors
from .time_tracker import TimeTracker
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
            on_refresh=self._request_refresh,
            on_monitor_change=self._on_monitor_change,
            on_close=self.shutdown,
        )

        # Exclude our own window from enumeration
        self._root.after(200, self._exclude_self)

        # Setup keyboard shortcuts
        self._widget.setup_keybindings(lambda: self._window_mgr.windows)

        # Start refresh loop
        self._schedule_refresh(500)

    def _exclude_self(self):
        try:
            hwnd = self._widget.get_hwnd()
            if hwnd:
                self._window_mgr.add_exclude(hwnd)
        except Exception:
            pass

    def _schedule_refresh(self, delay_ms):
        """Schedule exactly one pending refresh, cancelling any already queued.

        All refresh triggers — the periodic loop, the refresh button, and the
        post-action refreshes after tiling/minimize/restore — route through here
        so there is only ever a single refresh loop. Independent after() chains
        would multiply polling and double-count tracked time (each cycle adds a
        flat refresh_secs regardless of how much wall-clock actually elapsed).
        """
        if self._shutting_down:
            return
        if self._refresh_after_id is not None:
            try:
                self._root.after_cancel(self._refresh_after_id)
            except Exception:
                pass
        self._refresh_after_id = self._root.after(delay_ms, self._refresh)

    def _request_refresh(self):
        """Immediate user-triggered refresh (refresh button), folded into the
        single loop rather than spawning a parallel one."""
        self._schedule_refresh(0)

    def _refresh(self):
        """Enumerate windows and update the UI, then reschedule the single loop."""
        self._refresh_after_id = None  # the scheduled callback has now fired
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
            debug_log("_refresh")
        self._schedule_refresh(cfg.REFRESH_INTERVAL_MS)

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
        self._schedule_refresh(300)

    def _on_minimize_all(self):
        self._window_mgr.minimize_all()
        self._schedule_refresh(300)

    def _on_restore_all(self):
        self._window_mgr.restore_all()
        self._schedule_refresh(300)

    def _on_monitor_change(self, value):
        self._selected_monitor = value

    def shutdown(self):
        """Persist state, restore terminal borders, then hard-exit.

        A graceful exit is slow: the UIA reads accumulate COM proxies pointing
        into each Windows Terminal process, and comtypes' atexit CoUninitialize
        releases them synchronously (one cross-process IPC each), freezing the
        close for many seconds after a long session. Instead we save what
        matters and os._exit(), letting the OS reclaim the COM proxies and
        handles on process death — no Release() storm, no atexit freeze.
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

        # force_save closes the file (data flushed to the OS) so it survives the
        # hard exit; reset_all_borders is synchronous DWM calls that stick after
        # we're gone. Both must complete before os._exit.
        try:
            self._time_tracker.force_save()
        except Exception:
            pass

        try:
            self._widget.reset_all_borders()
        except Exception:
            pass

        os._exit(0)

    def run(self):
        self._root.mainloop()
