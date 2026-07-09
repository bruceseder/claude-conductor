import math
import tkinter as tk
from tkinter import font as tkfont

from . import config as cfg
from .utils import (
    lerp_color, set_window_border_color, reset_window_border_color,
    fetch_claude_status, fetch_claude_usage,
)


def _usage_color(pct):
    """Gauge fill color by how full a usage metric is."""
    if pct >= 95:
        return cfg.USAGE_COLOR_CRIT
    if pct >= 80:
        return cfg.USAGE_COLOR_HIGH
    if pct >= 50:
        return cfg.USAGE_COLOR_MID
    return cfg.USAGE_COLOR_LOW


def _fmt_time(seconds):
    """Format seconds as compact time string."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h{m:02d}m"
    return f"{m}m"


class PowerWidget(tk.Toplevel):
    def __init__(self, master, monitors, on_focus, on_tile, on_minimize_all,
                 on_restore_all, on_refresh, on_monitor_change, on_close=None):
        super().__init__(master)

        self._on_focus = on_focus
        self._on_tile = on_tile
        self._on_minimize_all = on_minimize_all
        self._on_restore_all = on_restore_all
        self._on_refresh = on_refresh
        self._on_monitor_change = on_monitor_change
        self._on_close_cb = on_close

        self._drag_x = 0
        self._drag_y = 0
        self._pinned = True
        self._minimized = False
        self._restore_tab = None
        self._window_rows = []
        self._monitors = monitors
        self._pulse_phase = 0.0
        self._pulse_rows = {}  # hwnd -> (row_frame, label, dot_canvas, dot_oval)
        self._pulse_running = False
        self._border_pulsing = set()  # hwnds with active border pulse
        self._last_border_color = {}  # hwnd -> last hex color sent to DWM
        self._border_frame_count = 0
        self._border_states = {}  # hwnd -> 'choice' | 'idle' | 'working' for border pulse

        self._nicknames = {}       # hwnd -> (nickname, title_at_assignment)
        self._editing_hwnd = None   # hwnd currently being renamed
        self._last_snapshot = None  # last per-row signature; lets update_window_list skip rebuilds

        self._setup_window()
        self._setup_fonts()
        self._build_ui()

    def _setup_window(self):
        # Create invisible (alpha=0) so any brief default-location mapping by
        # Windows can't show up as a ghost flash at upper-left. withdraw() is
        # unreliable here: Tk on Windows can map the Toplevel during
        # super().__init__() before we get a chance to withdraw, and toggling
        # overrideredirect forces a re-map. alpha=0 covers both cases.
        self.attributes('-alpha', 0.0)
        self.overrideredirect(True)
        self.attributes('-topmost', True)
        self.configure(bg=cfg.BG_COLOR)

        # Position bottom-right of primary monitor
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        self._x = screen_w - cfg.WIDGET_WIDTH - 20
        self._y = screen_h - cfg.WIDGET_MIN_HEIGHT - 80
        self.geometry(f'{cfg.WIDGET_WIDTH}x{cfg.WIDGET_MIN_HEIGHT}+{self._x}+{self._y}')

        self.minsize(cfg.WIDGET_WIDTH, cfg.WIDGET_MIN_HEIGHT)
        self.maxsize(cfg.WIDGET_WIDTH, cfg.WIDGET_MAX_HEIGHT)

        # Flush the geometry change before becoming visible.
        self.update_idletasks()
        self.attributes('-alpha', 0.95)

    def _setup_fonts(self):
        available = tkfont.families()
        family = cfg.FONT_FAMILY if cfg.FONT_FAMILY in available else cfg.FONT_FALLBACK
        self._font = tkfont.Font(family=family, size=cfg.FONT_SIZE)
        self._font_bold = tkfont.Font(family=family, size=cfg.FONT_SIZE, weight='bold')
        self._font_small = tkfont.Font(family=family, size=cfg.FONT_SIZE - 1)
        self._font_icon = tkfont.Font(family=family, size=cfg.FONT_SIZE + 2)

    def _build_ui(self):
        # Title bar
        self._build_title_bar()
        # Controls row
        self._build_controls()
        # Tile buttons
        self._build_tile_buttons()
        # Separator
        tk.Frame(self, bg=cfg.BORDER_COLOR, height=1).pack(fill='x', padx=8)
        # Status bar (pack before window list so it always has space)
        self._build_status_bar()
        # Usage stats bar (sits just above the status bar)
        self._build_usage_bar()
        # Window list (expands to fill remaining space)
        self._build_window_list()

    # --- Title Bar ---
    def _build_title_bar(self):
        bar = tk.Frame(self, bg=cfg.BG_SECONDARY, height=30)
        bar.pack(fill='x')
        bar.pack_propagate(False)

        # Drag handling
        bar.bind('<Button-1>', self._start_drag)
        bar.bind('<B1-Motion>', self._on_drag)

        # Icon + title
        lbl = tk.Label(bar, text=" \u26A1 Claude Conductor", font=self._font_bold,
                        bg=cfg.BG_SECONDARY, fg=cfg.ACCENT_COLOR, anchor='w')
        lbl.pack(side='left', padx=(8, 0), fill='y')
        lbl.bind('<Button-1>', self._start_drag)
        lbl.bind('<B1-Motion>', self._on_drag)

        # Close button
        close_btn = self._make_title_btn(bar, "\u2715", self._on_close)
        close_btn.pack(side='right', padx=(0, 4))

        # Pin button
        self._pin_btn = self._make_title_btn(bar, "\u25C9", self._toggle_pin)
        self._pin_btn.pack(side='right')

        # Minimize button
        min_btn = self._make_title_btn(bar, "\u2500", self._on_minimize_widget)
        min_btn.pack(side='right')

    def _make_title_btn(self, parent, text, command):
        btn = tk.Label(parent, text=text, font=self._font, bg=cfg.BG_SECONDARY,
                       fg=cfg.FG_DIM, cursor='hand2', padx=6)
        btn.bind('<Button-1>', lambda e: command())
        btn.bind('<Enter>', lambda e: btn.configure(fg=cfg.FG_COLOR))
        btn.bind('<Leave>', lambda e: btn.configure(fg=cfg.FG_DIM))
        return btn

    def _start_drag(self, event):
        self._drag_x = event.x_root - self.winfo_x()
        self._drag_y = event.y_root - self.winfo_y()

    def _on_drag(self, event):
        self._x = event.x_root - self._drag_x
        self._y = event.y_root - self._drag_y
        self.geometry(f'+{self._x}+{self._y}')

    def _toggle_pin(self):
        self._pinned = not self._pinned
        self.attributes('-topmost', self._pinned)
        self._pin_btn.configure(fg=cfg.ACCENT_COLOR if self._pinned else cfg.FG_DIM)

    def _on_close(self):
        if self._on_close_cb:
            self._on_close_cb()
        else:
            self.master.destroy()

    def reset_all_borders(self):
        """Reset DWM border colors for any windows we've been pulsing. Called
        on shutdown so Claude windows aren't left wearing widget colors."""
        self._pulse_running = False
        self._bolt_pulse_running = False
        for hwnd in list(self._border_pulsing) + list(self._last_border_color):
            try:
                reset_window_border_color(hwnd)
            except Exception:
                pass
        self._border_pulsing.clear()
        self._last_border_color.clear()

    def _on_minimize_widget(self):
        """Minimize by hiding the widget. Double-click tray area or use hotkey to restore."""
        self._minimized = True
        self._restore_pos = self.geometry()
        self.withdraw()
        # Show a small restore tab at the edge of the screen
        self._show_restore_tab()

    def _show_restore_tab(self):
        """Show a tiny clickable tab at screen edge to restore the widget."""
        if hasattr(self, '_restore_tab') and self._restore_tab:
            try:
                self._restore_tab.destroy()
            except tk.TclError:
                pass

        self._restore_tab = tk.Toplevel(self.master)
        # Create invisible to avoid the upper-left flash on creation —
        # see _setup_window for why withdraw() alone isn't enough on Windows.
        self._restore_tab.attributes('-alpha', 0.0)
        self._restore_tab.overrideredirect(True)
        self._restore_tab.attributes('-topmost', True)
        self._restore_tab.configure(bg=cfg.BG_COLOR)

        # Position on right edge of primary monitor, vertically centered
        screen_w = self.master.winfo_screenwidth()
        screen_h = self.master.winfo_screenheight()
        tab_w, tab_h = 40, 100
        self._restore_tab.geometry(f'{tab_w}x{tab_h}+{screen_w - tab_w}+{screen_h // 2 - tab_h // 2}')

        # overrideredirect strips the native frame, so DWM border coloring can't
        # apply here. Fake a border: the Toplevel's own bg shows through the
        # padding around this dark content frame, and the bolt loop pulses it
        # in the current state color.
        content = tk.Frame(self._restore_tab, bg=cfg.BG_COLOR)
        content.pack(fill='both', expand=True, padx=3, pady=3)

        family = self._font.actual('family')
        bolt_font = tkfont.Font(family=family, size=cfg.FONT_SIZE + 8, weight='bold')
        cc_font = tkfont.Font(family=family, size=cfg.FONT_SIZE + 2, weight='bold')

        bolt_lbl = tk.Label(content, text="\u26A1", font=bolt_font,
                            bg=cfg.BG_COLOR, fg=cfg.ACCENT_COLOR, cursor='hand2')
        bolt_lbl.pack(side='top', pady=(10, 0))
        cc_lbl = tk.Label(content, text="CC", font=cc_font,
                          bg=cfg.BG_COLOR, fg=cfg.FG_COLOR, cursor='hand2')
        cc_lbl.pack(side='top')

        for w in (self._restore_tab, content, bolt_lbl, cc_lbl):
            w.bind('<Button-1>', lambda e: self._restore_widget())

        self._restore_bolt_label = bolt_lbl
        self._restore_cc_label = cc_lbl

        # Flush the geometry change before becoming visible.
        self._restore_tab.update_idletasks()
        self._restore_tab.attributes('-alpha', 0.92)

        # Start the bolt pulse loop (self-terminates when widget is restored)
        if not getattr(self, '_bolt_pulse_running', False):
            self._bolt_pulse_phase = 0.0
            self._bolt_pulse_running = True
            self._animate_bolt()

    def _restore_widget(self):
        """Restore the widget from minimized state."""
        if hasattr(self, '_restore_tab') and self._restore_tab:
            try:
                self._restore_tab.destroy()
            except tk.TclError:
                pass
            self._restore_tab = None

        self._minimized = False
        self.deiconify()
        if hasattr(self, '_restore_pos') and self._restore_pos:
            self.geometry(self._restore_pos)
        self.attributes('-topmost', self._pinned)
        self.lift()

    # --- Controls Row ---
    def _build_controls(self):
        row = tk.Frame(self, bg=cfg.BG_COLOR)
        row.pack(fill='x', padx=8, pady=(6, 2))

        # Monitor selector
        tk.Label(row, text="Monitor:", font=self._font_small,
                 bg=cfg.BG_COLOR, fg=cfg.FG_DIM).pack(side='left')

        self._monitor_var = tk.StringVar(value="All")
        self._monitor_menu = tk.Menubutton(
            row, textvariable=self._monitor_var, font=self._font_small,
            bg=cfg.BUTTON_BG, fg=cfg.FG_COLOR, activebackground=cfg.BUTTON_HOVER,
            activeforeground=cfg.FG_COLOR, relief='flat', padx=6, pady=1,
            indicatoron=False, cursor='hand2'
        )
        self._monitor_menu.pack(side='left', padx=(4, 8))
        self._rebuild_monitor_menu()

        # Spacer
        tk.Frame(row, bg=cfg.BG_COLOR).pack(side='left', fill='x', expand=True)

        # Refresh button
        self._make_control_btn(row, "\u21BB", self._on_refresh).pack(side='left', padx=2)

        # Minimize all
        self._make_control_btn(row, "\u25BC", self._on_minimize_all).pack(side='left', padx=2)

        # Restore all
        self._make_control_btn(row, "\u25B2", self._on_restore_all).pack(side='left', padx=2)

    def _make_control_btn(self, parent, text, command):
        btn = tk.Label(parent, text=text, font=self._font_icon, bg=cfg.BUTTON_BG,
                       fg=cfg.FG_COLOR, padx=6, pady=0, cursor='hand2')
        btn.bind('<Button-1>', lambda e: command())
        btn.bind('<Enter>', lambda e: btn.configure(bg=cfg.BUTTON_HOVER))
        btn.bind('<Leave>', lambda e: btn.configure(bg=cfg.BUTTON_BG))
        return btn

    def _rebuild_monitor_menu(self):
        menu = tk.Menu(self._monitor_menu, tearoff=0,
                       bg=cfg.BUTTON_BG, fg=cfg.FG_COLOR,
                       activebackground=cfg.ACCENT_COLOR,
                       activeforeground=cfg.BG_COLOR,
                       font=self._font_small)

        menu.add_command(label="All", command=lambda: self._set_monitor("All"))
        menu.add_command(label="Distribute", command=lambda: self._set_monitor("Distribute"))
        menu.add_separator()

        for m in self._monitors:
            name = m.name
            menu.add_command(label=name, command=lambda n=name: self._set_monitor(n))

        self._monitor_menu.configure(menu=menu)

    def _set_monitor(self, value):
        self._monitor_var.set(value)
        self._on_monitor_change(value)

    # --- Tile Buttons ---
    def _build_tile_buttons(self):
        row = tk.Frame(self, bg=cfg.BG_COLOR)
        row.pack(fill='x', padx=8, pady=(2, 6))

        tiles = [
            ("\u25A6 Grid", 'grid'),
            ("\u2503 H-Split", 'horizontal'),
            ("\u2501 V-Split", 'vertical'),
            ("\u29C9 Cascade", 'cascade'),
        ]

        for label, mode in tiles:
            btn = tk.Label(row, text=label, font=self._font_small,
                          bg=cfg.BUTTON_BG, fg=cfg.FG_COLOR, padx=8, pady=3,
                          cursor='hand2')
            btn.pack(side='left', padx=2, expand=True, fill='x')
            btn.bind('<Button-1>', lambda e, m=mode: self._on_tile(m))
            btn.bind('<Enter>', lambda e, b=btn: b.configure(bg=cfg.ACCENT_COLOR, fg=cfg.BG_COLOR))
            btn.bind('<Leave>', lambda e, b=btn: b.configure(bg=cfg.BUTTON_BG, fg=cfg.FG_COLOR))

    # --- Window List ---
    def _build_window_list(self):
        self._list_frame = tk.Frame(self, bg=cfg.BG_COLOR)
        self._list_frame.pack(fill='both', expand=True, padx=4, pady=4)

        self._canvas = tk.Canvas(self._list_frame, bg=cfg.BG_COLOR,
                                  highlightthickness=0, bd=0)

        self._scrollbar = tk.Scrollbar(self._list_frame, orient='vertical',
                                        command=self._canvas.yview)

        self._inner_frame = tk.Frame(self._canvas, bg=cfg.BG_COLOR)
        self._inner_frame.bind('<Configure>',
                               lambda e: self._canvas.configure(scrollregion=self._canvas.bbox('all')))

        self._canvas_window = self._canvas.create_window((0, 0), window=self._inner_frame,
                                                          anchor='nw', width=cfg.WIDGET_WIDTH - 24)

        self._canvas.configure(yscrollcommand=self._scrollbar.set)
        self._canvas.pack(side='left', fill='both', expand=True)
        self._scrollbar.pack(side='right', fill='y')

        # Mouse wheel scrolling
        self._canvas.bind('<Enter>', self._bind_mousewheel)
        self._canvas.bind('<Leave>', self._unbind_mousewheel)

    def _bind_mousewheel(self, event):
        self._canvas.bind_all('<MouseWheel>', self._on_mousewheel)

    def _unbind_mousewheel(self, event):
        self._canvas.unbind_all('<MouseWheel>')

    def _on_mousewheel(self, event):
        self._canvas.yview_scroll(-1 * (event.delta // 120), 'units')

    # --- Status Bar ---
    def _build_status_bar(self):
        self._status_frame = tk.Frame(self, bg=cfg.BG_SECONDARY, height=24)
        self._status_frame.pack(fill='x', side='bottom')
        self._status_frame.pack_propagate(False)

        # Bottom-left readout: extra-usage spend ($used / $cap), set from the
        # usage poll. "--" until the first successful fetch.
        self._status_label = tk.Label(self._status_frame, text="--",
                                       font=self._font_small, bg=cfg.BG_SECONDARY,
                                       fg=cfg.FG_DIM, anchor='w')
        self._status_label.pack(side='left', padx=8, fill='y')

        # Resize grip
        grip = tk.Label(self._status_frame, text="\u2261", font=self._font_icon,
                        bg=cfg.BG_SECONDARY, fg=cfg.FG_DIM, cursor='sb_v_double_arrow')
        grip.pack(side='right', padx=4)
        grip.bind('<Button-1>', self._start_resize)
        grip.bind('<B1-Motion>', self._on_resize)

        # Claude network status indicators (right-aligned, before grip)
        # Colored dot + name; dot color = status (green/yellow/orange/red)
        self._net_status_labels = {}
        for short_name in reversed(list(cfg.STATUS_COMPONENTS.values())):
            lbl = tk.Label(self._status_frame, text=f"\u25CF{short_name}",
                           font=self._font_small, bg=cfg.BG_SECONDARY,
                           fg=cfg.FG_DIM, anchor='e')
            lbl.pack(side='right', padx=(0, 4), fill='y')
            self._net_status_labels[short_name] = lbl

        # Start polling
        self._poll_claude_status()

    def _start_resize(self, event):
        self._resize_y = event.y_root
        self._resize_h = self.winfo_height()

    def _on_resize(self, event):
        dy = event.y_root - self._resize_y
        new_h = max(cfg.WIDGET_MIN_HEIGHT, min(cfg.WIDGET_MAX_HEIGHT, self._resize_h + dy))
        self.geometry(f'{cfg.WIDGET_WIDTH}x{new_h}+{self._x}+{self._y}')

    def _poll_claude_status(self):
        """Fetch Claude network status in background, update UI on completion."""
        def _on_result(results):
            try:
                self.after(0, lambda: self._update_net_status(results))
            except Exception:
                pass

        fetch_claude_status(_on_result)
        self.after(cfg.STATUS_POLL_INTERVAL_MS, self._poll_claude_status)

    def _update_net_status(self, results):
        """Update the network status labels for each component."""
        for short_name, status, _label in results:
            color = cfg.STATUS_COLORS.get(status, cfg.FG_DIM)
            lbl = self._net_status_labels.get(short_name)
            if lbl:
                try:
                    lbl.configure(fg=color)
                except tk.TclError:
                    pass

    # --- Usage Stats Bar ---
    def _build_usage_bar(self):
        """Four mini-gauges (session / week / Fable / extra credits), polled per
        cfg.USAGE_POLL_INTERVAL_MS.

        Each cell: a label, a filling gauge colored by how full it is, a thin red
        pace marker showing where usage would be at even consumption, and a
        readout (percent, or for credits the dollars still available).
        """
        frame = tk.Frame(self, bg=cfg.BG_SECONDARY, height=cfg.USAGE_BAR_HEIGHT)
        frame.pack(fill='x', side='bottom')
        frame.pack_propagate(False)

        self._usage_cells = {}
        for key, label in cfg.USAGE_METRICS:
            cell = tk.Frame(frame, bg=cfg.BG_SECONDARY)
            cell.pack(side='left', expand=True, fill='both', padx=(2, 0))

            name = tk.Label(cell, text=label, font=self._font_small,
                            bg=cfg.BG_SECONDARY, fg=cfg.FG_COLOR)
            name.pack(side='left')

            canvas = tk.Canvas(cell, width=cfg.USAGE_GAUGE_W, height=cfg.USAGE_GAUGE_H,
                               bg=cfg.BG_SECONDARY, highlightthickness=0, bd=0)
            canvas.pack(side='left', padx=2)
            canvas.create_rectangle(0, 0, cfg.USAGE_GAUGE_W, cfg.USAGE_GAUGE_H,
                                    fill=cfg.USAGE_TRACK_COLOR, outline="")
            fill = canvas.create_rectangle(0, 0, 0, cfg.USAGE_GAUGE_H,
                                           fill=cfg.FG_DIM, outline="")
            # Pace marker drawn last so it sits on top of the fill; hidden until known.
            pace = canvas.create_line(0, 0, 0, cfg.USAGE_GAUGE_H,
                                      fill=cfg.USAGE_PACE_COLOR, width=1, state='hidden')

            pct = tk.Label(cell, text="--", font=self._font_small, bg=cfg.BG_SECONDARY,
                           fg=cfg.FG_DIM, width=4, anchor='w')
            pct.pack(side='left')

            self._usage_cells[key] = {"name": name, "canvas": canvas,
                                      "fill": fill, "pace": pace, "pct": pct}

        self._usage_interval_ms = cfg.USAGE_POLL_INTERVAL_MS
        self._poll_claude_usage()

    def _poll_claude_usage(self):
        """Kick a background usage fetch, then reschedule at the current interval
        (which backs off while rate-limited)."""
        def _on_result(metrics, status, spend_text):
            try:
                self.after(0, lambda: self._on_usage_result(metrics, status, spend_text))
            except Exception:
                pass

        fetch_claude_usage(_on_result)
        self.after(self._usage_interval_ms, self._poll_claude_usage)

    def _on_usage_result(self, metrics, status, spend_text):
        """Apply a fetch result: update gauges + the bottom-left spend readout on
        success, otherwise keep the last-good values (never blank on a transient
        failure) and back off the poll interval when the endpoint rate-limits us."""
        if status == "ok" and metrics is not None:
            self._usage_interval_ms = cfg.USAGE_POLL_INTERVAL_MS  # recovered — reset backoff
            self._update_usage(metrics)
            if spend_text:
                self._status_label.configure(text=spend_text)  # bottom-left = extra $used/$cap
        elif status == "rate_limited":
            self._usage_interval_ms = min(self._usage_interval_ms * 2,
                                          cfg.USAGE_MAX_POLL_INTERVAL_MS)
        # "error" (network/token): keep last-good gauges + spend text, leave interval as-is

    def _update_usage(self, results):
        """Redraw each gauge fill, pace marker, and readout from fetched results."""
        for key, label, pct, pace, text in results:
            cell = self._usage_cells.get(key)
            if not cell:
                continue
            try:
                cell["name"].configure(text=label)
                canvas = cell["canvas"]
                if pct is None:
                    canvas.coords(cell["fill"], 0, 0, 0, cfg.USAGE_GAUGE_H)
                    canvas.itemconfigure(cell["pace"], state='hidden')
                    cell["pct"].configure(text="--", fg=cfg.FG_DIM)
                else:
                    p = max(0.0, min(100.0, float(pct)))
                    color = _usage_color(p)
                    canvas.coords(cell["fill"], 0, 0, cfg.USAGE_GAUGE_W * p / 100.0, cfg.USAGE_GAUGE_H)
                    canvas.itemconfigure(cell["fill"], fill=color)
                    cell["pct"].configure(text=text if text is not None else f"{int(round(p))}%", fg=color)
                    if pace is None:
                        canvas.itemconfigure(cell["pace"], state='hidden')
                    else:
                        x = cfg.USAGE_GAUGE_W * max(0.0, min(100.0, float(pace))) / 100.0
                        canvas.coords(cell["pace"], x, 0, x, cfg.USAGE_GAUGE_H)
                        canvas.itemconfigure(cell["pace"], state='normal')
            except tk.TclError:
                pass

    @staticmethod
    def _state_colors(state):
        """Return (main, bright, dim) for a window/bolt state.

        working -> electric blue, choice (waiting) -> HDR orange,
        idle (done) -> HDR green.
        """
        if state == 'choice':
            return cfg.ATTENTION_COLOR, cfg.ATTENTION_COLOR_BRIGHT, cfg.ATTENTION_COLOR_DIM
        if state == 'working':
            return cfg.WORKING_COLOR, cfg.WORKING_COLOR_BRIGHT, cfg.WORKING_COLOR_DIM
        return cfg.IDLE_COLOR, cfg.IDLE_COLOR_BRIGHT, cfg.IDLE_COLOR_DIM

    def _compute_bolt_state(self, windows):
        """Aggregate restore-tab state across all windows. The bolt-pulse loop reads this."""
        has_choice = any(w.needs_attention and w.attention_type == 'choice' for w in windows)
        has_working = any(w.is_claude and not w.needs_attention for w in windows)
        if has_choice:
            self._bolt_state = 'choice'
        elif has_working:
            self._bolt_state = 'working'
        else:
            self._bolt_state = 'idle'

    # --- Public API ---
    def update_window_list(self, windows, time_tracker=None):
        """Rebuild the window list with current windows."""
        # Don't rebuild while user is renaming a window
        if self._editing_hwnd is not None:
            return

        # Bolt state can change every tick; the bolt loop reads it at 12fps regardless of rebuild.
        self._compute_bolt_state(windows)

        # Per-row signature: skip the full rebuild when nothing visible has changed.
        # Captures everything that affects rendered rows + ordering. Time is bucketed
        # to the minute so per-second updates don't trigger rebuilds.
        snapshot = tuple(
            (w.hwnd, w.display_title, bool(self._nicknames.get(w.hwnd)),
             w.needs_attention, w.attention_type, w.is_minimized,
             (time_tracker.get_today_seconds(w.hwnd) // 60) if time_tracker else 0)
            for w in windows
        )
        if snapshot == self._last_snapshot:
            return
        self._last_snapshot = snapshot

        # Clear existing rows
        for w in self._inner_frame.winfo_children():
            w.destroy()

        self._window_rows = []

        # Every Claude window gets a state-colored border: waiting/done windows
        # pulse orange/green, actively-working windows pulse electric blue.
        self._border_states = {}
        for w in windows:
            if w.needs_attention:
                self._border_states[w.hwnd] = 'choice' if w.attention_type == 'choice' else 'idle'
            elif w.is_claude:
                self._border_states[w.hwnd] = 'working'
        border_hwnds = set(self._border_states)

        # Reset borders for windows that are gone (closed / no longer tracked)
        stale = self._border_pulsing - border_hwnds
        for hwnd in stale:
            reset_window_border_color(hwnd)
        self._border_pulsing -= stale
        # Clean up stale color cache
        for hwnd in list(self._last_border_color):
            if hwnd not in border_hwnds:
                del self._last_border_color[hwnd]

        self._pulse_rows = {}

        for i, win in enumerate(windows):
            row = tk.Frame(self._inner_frame, bg=cfg.BG_COLOR, height=cfg.ROW_HEIGHT)
            row.pack(fill='x', pady=1)
            row.pack_propagate(False)

            # Color based on state: choice -> orange, idle/done -> green,
            # working -> electric blue (matches the bolt and window borders).
            if win.needs_attention and win.attention_type == 'choice':
                base_color = cfg.ATTENTION_COLOR
            elif win.needs_attention:
                base_color = cfg.IDLE_COLOR
            elif win.is_claude:
                base_color = cfg.WORKING_COLOR
            else:
                base_color = cfg.FG_DIM

            dot = tk.Canvas(row, width=12, height=12, bg=cfg.BG_COLOR,
                           highlightthickness=0)
            dot.pack(side='left', padx=(8, 4), pady=0)
            dot_oval = dot.create_oval(2, 2, 10, 10, fill=base_color, outline='')

            # Title (use nickname if set)
            raw_title = win.display_title or win.title
            nickname = self._get_nickname(win.hwnd, raw_title)
            title = nickname or raw_title
            if len(title) > 38:
                title = title[:36] + "\u2026"

            fg = base_color if win.needs_attention else cfg.FG_COLOR
            lbl = tk.Label(row, text=title, font=self._font_bold if win.needs_attention else self._font,
                          bg=cfg.BG_COLOR, fg=fg, anchor='w')
            lbl.pack(side='left', fill='x', expand=True, padx=(0, 4))

            # Track non-main labels so the pulse loop can update their bg
            # without iterating winfo_children() at every frame.
            extra_labels = []

            # Attention indicator with type hint
            if win.needs_attention:
                indicator = "\u2753" if win.attention_type == 'choice' else "\u2713"  # ? vs checkmark
                attn_lbl = tk.Label(row, text=indicator, font=self._font_small,
                                    bg=cfg.BG_COLOR, fg=base_color)
                attn_lbl.pack(side='right', padx=2)
                extra_labels.append(attn_lbl)

            # Minimized indicator
            if win.is_minimized:
                min_lbl = tk.Label(row, text="\u2500", font=self._font_small,
                                   bg=cfg.BG_COLOR, fg=cfg.FG_DIM)
                min_lbl.pack(side='right', padx=4)
                extra_labels.append(min_lbl)

            # Time tracking label
            if time_tracker:
                secs = time_tracker.get_today_seconds(win.hwnd)
                if secs > 0:
                    time_lbl = tk.Label(row, text=_fmt_time(secs), font=self._font_small,
                                        bg=cfg.BG_COLOR, fg=cfg.FG_DIM)
                    time_lbl.pack(side='right', padx=(0, 4))
                    extra_labels.append(time_lbl)

            # Number shortcut label
            if i < 9:
                num_lbl = tk.Label(row, text=str(i + 1), font=self._font_small,
                                   bg=cfg.BG_COLOR, fg=cfg.FG_DIM)
                num_lbl.pack(side='right', padx=(0, 6))
                extra_labels.append(num_lbl)

            # Bind click to focus (label uses delayed click so double-click can rename)
            hwnd = win.hwnd
            raw_t = raw_title
            for widget in [row, dot]:
                widget.bind('<Button-1>', lambda e, h=hwnd: self._on_focus(h))

            lbl.bind('<Button-1>', lambda e, h=hwnd: self._on_focus(h))

            # Right-click to rename
            raw_t = raw_title
            lbl.bind('<Button-3>', lambda e, h=hwnd, dt=raw_t, r=row, l=lbl: self._start_rename(h, dt, r, l))

            # Hover only for non-attention rows (attention rows pulse instead)
            if not win.needs_attention:
                for widget in [row, lbl, dot]:
                    widget.bind('<Enter>', lambda e, r=row, l=lbl: (
                        r.configure(bg=cfg.HOVER_COLOR),
                        l.configure(bg=cfg.HOVER_COLOR),
                    ))
                    widget.bind('<Leave>', lambda e, r=row, l=lbl: (
                        r.configure(bg=cfg.BG_COLOR),
                        l.configure(bg=cfg.BG_COLOR),
                    ))

            # Register for pulse animation with type
            if win.needs_attention:
                self._pulse_rows[win.hwnd] = (row, lbl, dot, dot_oval, win.attention_type, extra_labels)

            self._window_rows.append((win.hwnd, row))

        # Start or stop pulse animation (drives both list-row glow and DWM borders)
        has_pulse = bool(self._pulse_rows or self._border_states)
        if has_pulse and not self._pulse_running:
            self._pulse_running = True
            self._animate_pulse()
        elif not has_pulse:
            self._pulse_running = False

        # (Bottom-left status label now shows extra-usage spend, set by the usage
        # poll in _on_usage_result \u2014 no longer the window count.)

        # Auto-resize height based on content. Always include +x+y — calling
        # geometry() with size only on an overrideredirect+topmost+layered
        # window can briefly snap it to (0,0) before DWM bounces it back.
        desired_h = 130 + cfg.USAGE_BAR_HEIGHT + len(windows) * (cfg.ROW_HEIGHT + 1)
        desired_h = max(cfg.WIDGET_MIN_HEIGHT, min(cfg.WIDGET_MAX_HEIGHT, desired_h))
        current_h = self.winfo_height()
        if abs(desired_h - current_h) > 20:
            self.geometry(f'{cfg.WIDGET_WIDTH}x{desired_h}+{self._x}+{self._y}')


    def _animate_pulse(self):
        """Pulsing glow for attention list rows AND every window's DWM border.

        List rows pulse only for windows that need attention (choice/idle).
        Borders pulse for all three states: working -> electric blue,
        choice -> HDR orange, idle -> HDR green.
        """
        if not self._pulse_running or (not self._pulse_rows and not self._border_states):
            self._pulse_running = False
            # Reset any lingering border colors
            for hwnd in list(self._border_pulsing):
                reset_window_border_color(hwnd)
            self._border_pulsing.clear()
            self._last_border_color.clear()
            return

        self._pulse_phase += cfg.PULSE_SPEED
        # Sine wave: 0..1..0 smoothly
        t = (math.sin(self._pulse_phase) + 1.0) / 2.0

        # Only update DWM borders every 6th frame (~300ms) to avoid flicker
        self._border_frame_count += 1
        update_borders = (self._border_frame_count % 6) == 0

        # List-row glow — attention rows only. Skip the per-row Tk reconfigures
        # while minimized: the rows are hidden behind the restore tab, so only
        # the DWM borders (below) are visible. The bolt loop handles the tab.
        if not self._minimized:
            for hwnd, (row, lbl, dot_canvas, dot_oval, atype, extras) in self._pulse_rows.items():
                color_main, color_bright, color_dim = self._state_colors(atype)
                bg = lerp_color(cfg.BG_COLOR, color_dim, t)
                glow = lerp_color(color_main, color_bright, t)
                try:
                    row.configure(bg=bg)
                    lbl.configure(bg=bg, fg=glow)
                    dot_canvas.configure(bg=bg)
                    dot_canvas.itemconfigure(dot_oval, fill=glow)
                    for child in extras:
                        child.configure(bg=bg)
                except tk.TclError:
                    pass

        # Pulse the actual window border + title bar via DWM (throttled), one
        # color per state across every Claude window.
        if update_borders:
            for hwnd, state in self._border_states.items():
                color_main, color_bright, color_dim = self._state_colors(state)
                border_color = lerp_color(color_main, color_bright, t)
                caption_color = lerp_color(color_dim, color_main, t)
                cache_key = (border_color, caption_color)
                if self._last_border_color.get(hwnd) != cache_key:
                    set_window_border_color(hwnd, border_color, caption_color)
                    self._last_border_color[hwnd] = cache_key
                self._border_pulsing.add(hwnd)

        self.after(cfg.PULSE_INTERVAL_MS, self._animate_pulse)

    def _animate_bolt(self):
        """Pulse the minimized restore tab bolt in the color of the current aggregate state."""
        if not self._minimized or not getattr(self, '_restore_bolt_label', None):
            self._bolt_pulse_running = False
            return

        self._bolt_pulse_phase += cfg.PULSE_SPEED
        t = (math.sin(self._bolt_pulse_phase) + 1.0) / 2.0
        state = getattr(self, '_bolt_state', 'idle')
        main, bright, _dim = self._state_colors(state)
        c = lerp_color(main, bright, t)

        try:
            self._restore_bolt_label.configure(fg=c)
            if self._restore_tab:
                self._restore_tab.configure(bg=c)
        except tk.TclError:
            pass

        self.after(cfg.PULSE_INTERVAL_MS, self._animate_bolt)

    def update_monitors(self, monitors):
        """Update the monitor selector options."""
        self._monitors = monitors
        self._rebuild_monitor_menu()

    def get_nicknamed_hwnds(self):
        """Return set of hwnds that have active user-assigned nicknames."""
        return set(self._nicknames.keys())

    def get_hwnd(self):
        """Get the widget's own window handle."""
        return int(self.frame(), 16)

    def setup_keybindings(self, windows_getter):
        """Bind keyboard shortcuts."""
        self.master.bind('<Escape>', lambda e: self._on_minimize_widget())

        for i in range(9):
            self.master.bind(f'<Control-Key-{i + 1}>',
                           lambda e, idx=i: self._focus_by_index(idx, windows_getter))

        self.master.bind('<Control-g>', lambda e: self._on_tile('grid'))
        self.master.bind('<Control-h>', lambda e: self._on_tile('horizontal'))
        # Control-v is paste, use Control-j for vertical
        self.master.bind('<Control-j>', lambda e: self._on_tile('vertical'))

    def _focus_by_index(self, index, windows_getter):
        windows = windows_getter()
        if index < len(windows):
            self._on_focus(windows[index].hwnd)

    # --- Nickname Management ---
    def _get_nickname(self, hwnd, display_title):
        """Return nickname if set and the underlying title hasn't changed."""
        if hwnd in self._nicknames:
            nickname, title_at_assignment = self._nicknames[hwnd]
            if display_title == title_at_assignment:
                return nickname
            # Title changed externally (e.g. Claude /rename) — clear the override
            del self._nicknames[hwnd]
        return None

    def _start_rename(self, hwnd, display_title, row, lbl):
        """Replace the title label with an Entry for inline editing."""
        if self._editing_hwnd is not None:
            return
        self._editing_hwnd = hwnd

        current = self._get_nickname(hwnd, display_title) or display_title
        lbl.pack_forget()

        entry = tk.Entry(row, font=self._font, bg=cfg.BUTTON_BG, fg=cfg.FG_COLOR,
                         insertbackground=cfg.FG_COLOR, relief='flat',
                         selectbackground=cfg.ACCENT_COLOR)
        entry.insert(0, current)
        entry.select_range(0, 'end')
        entry.pack(side='left', fill='x', expand=True, padx=(0, 4))
        entry.focus_set()

        def finish(event=None):
            new_name = entry.get().strip()
            entry.destroy()
            lbl.pack(side='left', fill='x', expand=True, padx=(0, 4))
            self._editing_hwnd = None

            if new_name and new_name != display_title:
                self._nicknames[hwnd] = (new_name, display_title)
                nick_display = new_name if len(new_name) <= 38 else new_name[:36] + "\u2026"
                lbl.configure(text=nick_display)
            elif not new_name or new_name == display_title:
                # Clear nickname
                self._nicknames.pop(hwnd, None)
                title_display = display_title if len(display_title) <= 38 else display_title[:36] + "\u2026"
                lbl.configure(text=title_display)

        def cancel(event=None):
            entry.destroy()
            lbl.pack(side='left', fill='x', expand=True, padx=(0, 4))
            self._editing_hwnd = None

        entry.bind('<Return>', finish)
        entry.bind('<Escape>', cancel)
        entry.bind('<FocusOut>', finish)
