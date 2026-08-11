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


def _fit_text(text, font, max_w):
    """Truncate text with an ellipsis so it fits max_w pixels."""
    if font.measure(text) <= max_w:
        return text
    while text and font.measure(text + "…") > max_w:
        text = text[:-1]
    return text + "…"


# Horizontal center of each background strip, as a fraction of row width. The
# pulse phase is lagged by this, which is what makes the glow roll rightward.
_STRIP_FRACS = tuple((i + 0.5) / cfg.PULSE_SWEEP_STRIPS
                     for i in range(cfg.PULSE_SWEEP_STRIPS))


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
        self._row_info = {}    # hwnd -> row render info (see _make_row)
        self._pulse_rows = {}  # hwnd -> the same info, for pulsing rows only
        self._pulse_running = False
        self._pulse_after_id = None  # the single pending _animate_pulse callback
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
        """Populate the monitor dropdown, reusing a single tk.Menu.

        update_monitors() calls this every ~30s; creating a fresh tk.Menu each
        time (and only re-pointing the Menubutton at it) orphans the old menu,
        which stays a child of the Menubutton and leaks a Windows USER object per
        rebuild. Instead we build the menu once and repopulate it in place, and
        skip the work entirely when the monitor list hasn't changed.
        """
        names = [m.name for m in self._monitors]
        if names == getattr(self, '_monitor_menu_names', None):
            return
        self._monitor_menu_names = names

        menu = getattr(self, '_monitor_dropdown', None)
        if menu is None:
            menu = tk.Menu(self._monitor_menu, tearoff=0,
                           bg=cfg.BUTTON_BG, fg=cfg.FG_COLOR,
                           activebackground=cfg.ACCENT_COLOR,
                           activeforeground=cfg.BG_COLOR,
                           font=self._font_small)
            self._monitor_dropdown = menu
            self._monitor_menu.configure(menu=menu)
        else:
            menu.delete(0, 'end')

        menu.add_command(label="All", command=lambda: self._set_monitor("All"))
        menu.add_command(label="Distribute", command=lambda: self._set_monitor("Distribute"))
        menu.add_separator()
        for name in names:
            menu.add_command(label=name, command=lambda n=name: self._set_monitor(n))

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

        # Bottom-left: extra-usage gauge ("Extra") + $used/$cap, shares this row
        # with the Code/API/Web indicators. Set from the usage poll (in
        # _on_usage_result); "--" until the first successful fetch.
        cell, self._credits_cell = self._make_gauge_cell(self._status_frame, "Extra",
                                                         readout_width=13)
        cell.pack(side='left', padx=8)

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
    def _make_gauge_cell(self, parent, label, readout_width):
        """Create a labeled mini-gauge (track + fill + hidden pace marker + readout)
        in a new cell frame. Returns (cell_frame, gauge_dict); the caller packs the
        frame. Shared by the usage row and the status-bar extra-credits gauge."""
        cell = tk.Frame(parent, bg=cfg.BG_SECONDARY)
        name = tk.Label(cell, text=label, font=self._font_small,
                        bg=cfg.BG_SECONDARY, fg=cfg.FG_COLOR)
        name.pack(side='left')
        canvas = tk.Canvas(cell, width=cfg.USAGE_GAUGE_W, height=cfg.USAGE_GAUGE_H,
                           bg=cfg.BG_SECONDARY, highlightthickness=0, bd=0)
        canvas.pack(side='left', padx=3)
        canvas.create_rectangle(0, 0, cfg.USAGE_GAUGE_W, cfg.USAGE_GAUGE_H,
                                fill=cfg.USAGE_TRACK_COLOR, outline="")
        fill = canvas.create_rectangle(0, 0, 0, cfg.USAGE_GAUGE_H, fill=cfg.FG_DIM, outline="")
        # Pace marker drawn last so it sits on top of the fill; hidden until known.
        pace = canvas.create_line(0, 0, 0, cfg.USAGE_GAUGE_H,
                                  fill=cfg.USAGE_PACE_COLOR, width=1, state='hidden')
        readout = tk.Label(cell, text="--", font=self._font_small, bg=cfg.BG_SECONDARY,
                           fg=cfg.FG_DIM, width=readout_width, anchor='w')
        readout.pack(side='left')
        return cell, {"name": name, "canvas": canvas, "fill": fill, "pace": pace, "pct": readout}

    def _render_gauge(self, cell, pct, pace, text):
        """Draw one gauge: fill width/color by pct, optional pace marker, and the
        readout text (falls back to '<pct>%'). pct=None blanks the cell to '--'."""
        canvas = cell["canvas"]
        if pct is None:
            canvas.coords(cell["fill"], 0, 0, 0, cfg.USAGE_GAUGE_H)
            canvas.itemconfigure(cell["pace"], state='hidden')
            cell["pct"].configure(text="--", fg=cfg.FG_DIM)
            return
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

    def _build_usage_bar(self):
        """Three mini-gauges (session / week / Fable), polled per
        cfg.USAGE_POLL_INTERVAL_MS. The extra-usage credits gauge lives in the
        status bar (see _build_status_bar). Each cell: a label, a filling gauge
        colored by how full it is, a thin red pace marker, and a percent."""
        frame = tk.Frame(self, bg=cfg.BG_SECONDARY, height=cfg.USAGE_BAR_HEIGHT)
        frame.pack(fill='x', side='bottom')
        frame.pack_propagate(False)

        self._usage_cells = {}
        for key, label in cfg.USAGE_METRICS:
            cell, gauge = self._make_gauge_cell(frame, label, readout_width=4)
            cell.pack(side='left', expand=True, fill='both', padx=(4, 0))
            self._usage_cells[key] = gauge

        self._usage_interval_ms = cfg.USAGE_POLL_INTERVAL_MS
        self._poll_claude_usage()

    def _poll_claude_usage(self):
        """Kick a background usage fetch, then reschedule at the current interval
        (which backs off while rate-limited)."""
        def _on_result(metrics, status, spend):
            try:
                self.after(0, lambda: self._on_usage_result(metrics, status, spend))
            except Exception:
                pass

        fetch_claude_usage(_on_result)
        self.after(self._usage_interval_ms, self._poll_claude_usage)

    def _on_usage_result(self, metrics, status, spend):
        """Apply a fetch result: update the usage gauges + the status-bar extra
        gauge/spend on success, otherwise keep the last-good values (never blank on
        a transient failure) and back off the interval when rate-limited.

        spend is (used_percent, "$used / $cap") or None."""
        if status == "ok" and metrics is not None:
            self._usage_interval_ms = cfg.USAGE_POLL_INTERVAL_MS  # recovered — reset backoff
            self._update_usage(metrics)
            if spend is not None:
                try:
                    self._render_gauge(self._credits_cell, spend[0], None, spend[1])
                except tk.TclError:
                    pass
        elif status == "rate_limited":
            self._usage_interval_ms = min(self._usage_interval_ms * 2,
                                          cfg.USAGE_MAX_POLL_INTERVAL_MS)
        # "error" (network/token): keep last-good gauges + spend, leave interval as-is

    def _update_usage(self, results):
        """Redraw each usage gauge from fetched (key, label, pct, pace, text) tuples."""
        for key, label, pct, pace, text in results:
            cell = self._usage_cells.get(key)
            if not cell:
                continue
            try:
                cell["name"].configure(text=label)
                self._render_gauge(cell, pct, pace, text)
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
        self._row_info = {}

        for i, win in enumerate(windows):
            info = self._make_row(i, win, time_tracker)
            self._row_info[win.hwnd] = info
            # Every Claude row pulses: choice/idle in their attention color,
            # working in electric blue. Non-Claude rows stay static.
            if info['state']:
                self._pulse_rows[win.hwnd] = info
            self._window_rows.append((win.hwnd, info['canvas']))

        # Start or stop pulse animation (drives both list-row glow and DWM borders).
        # Only kick a fresh chain when none is already pending: if a callback is
        # still queued (rows briefly drained then reappeared before it fired), it
        # resumes the chain once it sees _pulse_running back on — starting another
        # here would leave two chains running at once (double pulse rate).
        has_pulse = bool(self._pulse_rows or self._border_states)
        if has_pulse:
            self._pulse_running = True
            if self._pulse_after_id is None:
                self._animate_pulse()
        else:
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

    def _make_row(self, i, win, time_tracker):
        """Draw one window row and return its render info.

        The row is a single Canvas rather than a Frame of Labels: its background
        is a strip of vertical bands the pulse loop recolors independently, which
        is what lets the glow roll left-to-right. Labels can only hold one flat
        background color, so the roll isn't expressible with them.
        """
        # Row width is fixed: the widget can't be resized horizontally
        # (minsize/maxsize pin it) and the inner frame is pinned in
        # _build_window_list.
        row_w = cfg.WIDGET_WIDTH - 24
        mid_y = cfg.ROW_HEIGHT // 2

        row = tk.Canvas(self._inner_frame, height=cfg.ROW_HEIGHT, width=row_w,
                        bg=cfg.BG_COLOR, highlightthickness=0, bd=0)
        row.pack(fill='x', pady=1)

        # State drives every color on the row: choice -> orange, idle/done ->
        # green, working -> electric blue (matches the bolt and window borders).
        if win.needs_attention:
            state = 'choice' if win.attention_type == 'choice' else 'idle'
        elif win.is_claude:
            state = 'working'
        else:
            state = None
        base_color = self._state_colors(state)[0] if state else cfg.FG_DIM

        # Background bands, drawn first so everything else sits on top of them.
        strips = []
        for s in range(cfg.PULSE_SWEEP_STRIPS):
            x0 = round(s * row_w / cfg.PULSE_SWEEP_STRIPS)
            x1 = round((s + 1) * row_w / cfg.PULSE_SWEEP_STRIPS)
            strips.append(row.create_rectangle(x0, 0, x1, cfg.ROW_HEIGHT,
                                               fill=cfg.BG_COLOR, outline=''))

        # Items the pulse recolors, each with the x fraction it samples the
        # travelling wave at, so they light up in step with the band behind them.
        glow = []

        # Status dot, at the same spot the old 12px dot canvas put it.
        glow.append((row.create_oval(10, 12, 18, 20, fill=base_color, outline=''),
                     14 / row_w))

        # Right-aligned items, laid out right-to-left in the order they used to
        # be packed, with the same padding: (text, color, (pad_l, pad_r), glows).
        right_items = []
        if win.needs_attention:
            indicator = "❓" if win.attention_type == 'choice' else "✓"  # ? vs checkmark
            right_items.append((indicator, base_color, (2, 2), True))
        if win.is_minimized:
            right_items.append(("─", cfg.FG_DIM, (4, 4), False))
        if time_tracker:
            secs = time_tracker.get_today_seconds(win.hwnd)
            if secs > 0:
                right_items.append((_fmt_time(secs), cfg.FG_DIM, (0, 4), False))
        if i < 9:
            right_items.append((str(i + 1), cfg.FG_DIM, (0, 6), False))

        right_x = row_w
        for text, color, (pad_l, pad_r), glows in right_items:
            right_x -= pad_r
            item = row.create_text(right_x, mid_y, text=text, font=self._font_small,
                                   fill=color, anchor='e')
            text_w = self._font_small.measure(text)
            if glows:
                glow.append((item, (right_x - text_w / 2) / row_w))
            right_x -= text_w + pad_l

        # Title (use nickname if set), filling what the right-hand items leave.
        raw_title = win.display_title or win.title
        title = self._get_nickname(win.hwnd, raw_title) or raw_title
        title_x = 24  # 8px left pad + the 12px dot + its 4px gap
        title_w = max(20, right_x - 4 - title_x)
        title_font = self._font_bold if win.needs_attention else self._font
        fitted = _fit_text(title, title_font, title_w)
        title_item = row.create_text(title_x, mid_y, text=fitted, font=title_font,
                                     fill=base_color if win.needs_attention else cfg.FG_COLOR,
                                     anchor='w')
        if win.needs_attention:
            glow.append((title_item,
                         (title_x + title_font.measure(fitted) / 2) / row_w))

        info = {'canvas': row, 'strips': strips, 'glow': glow, 'state': state,
                'title_item': title_item, 'title_x': title_x, 'title_w': title_w,
                'title_font': title_font, 'raw_title': raw_title}

        hwnd = win.hwnd
        row.bind('<Button-1>', lambda e, h=hwnd: self._on_focus(h))
        # Right-click the title to rename (item binding, so only the title area)
        row.tag_bind(title_item, '<Button-3>', lambda e, h=hwnd: self._start_rename(h))

        # Hover only for rows that don't pulse — a pulsing row is already lit.
        if not state:
            row.bind('<Enter>', lambda e, r=info: self._paint_row(r, cfg.HOVER_COLOR))
            row.bind('<Leave>', lambda e, r=info: self._paint_row(r, cfg.BG_COLOR))

        return info

    @staticmethod
    def _paint_row(info, color):
        """Flat-fill a row's background bands (hover / static rows)."""
        canvas = info['canvas']
        try:
            for item in info['strips']:
                canvas.itemconfigure(item, fill=color)
        except tk.TclError:
            pass

    def _sweep_t(self, frac):
        """Pulse level 0..1 at a horizontal position of the row.

        The phase lags with distance from the left edge, so the peak reaches the
        right side later than the left — the glow rolls across instead of fading
        everywhere at once.
        """
        return (math.sin(self._pulse_phase - frac * cfg.PULSE_SWEEP_LAG) + 1.0) / 2.0

    def _animate_pulse(self):
        """Rolling glow for every Claude list row AND every window's DWM border.

        List rows pulse in all three states, the glow rolling left-to-right.
        Borders pulse in the same three colors but flat across the frame
        (DWM takes one color): working -> electric blue, choice -> HDR orange,
        idle -> HDR green.
        """
        self._pulse_after_id = None  # this callback has now fired
        if not self._pulse_running or (not self._pulse_rows and not self._border_states):
            self._pulse_running = False
            # Reset any lingering border colors
            for hwnd in list(self._border_pulsing):
                reset_window_border_color(hwnd)
            self._border_pulsing.clear()
            self._last_border_color.clear()
            return

        self._pulse_phase += cfg.PULSE_SPEED

        # Only update DWM borders every 6th frame (~300ms) to avoid flicker
        self._border_frame_count += 1
        update_borders = (self._border_frame_count % 6) == 0

        # List-row glow. Skip the per-row Tk reconfigures while minimized: the
        # rows are hidden behind the restore tab, so only the DWM borders
        # (below) are visible. The bolt loop handles the tab.
        if not self._minimized:
            # The band colors depend only on state and strip position, so build
            # one palette per state per frame and share it across every row.
            palettes = {}
            for info in self._pulse_rows.values():
                state = info['state']
                if state not in palettes:
                    color_main, color_bright, color_dim = self._state_colors(state)
                    palettes[state] = (
                        [lerp_color(cfg.BG_COLOR, color_dim, self._sweep_t(f))
                         for f in _STRIP_FRACS],
                        color_main, color_bright,
                    )
                bands, color_main, color_bright = palettes[state]
                canvas = info['canvas']
                try:
                    for item, color in zip(info['strips'], bands):
                        canvas.itemconfigure(item, fill=color)
                    for item, frac in info['glow']:
                        canvas.itemconfigure(
                            item, fill=lerp_color(color_main, color_bright,
                                                  self._sweep_t(frac)))
                except tk.TclError:
                    pass

        # Pulse the actual window border + title bar via DWM (throttled), one
        # color per state across every Claude window.
        if update_borders:
            # Sine wave: 0..1..0 smoothly, unlagged — a border is one flat color.
            t = (math.sin(self._pulse_phase) + 1.0) / 2.0
            for hwnd, state in self._border_states.items():
                color_main, color_bright, color_dim = self._state_colors(state)
                border_color = lerp_color(color_main, color_bright, t)
                caption_color = lerp_color(color_dim, color_main, t)
                cache_key = (border_color, caption_color)
                if self._last_border_color.get(hwnd) != cache_key:
                    set_window_border_color(hwnd, border_color, caption_color)
                    self._last_border_color[hwnd] = cache_key
                self._border_pulsing.add(hwnd)

        self._pulse_after_id = self.after(cfg.PULSE_INTERVAL_MS, self._animate_pulse)

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

    def _start_rename(self, hwnd):
        """Hide the title text item and float an Entry over it for inline editing."""
        info = self._row_info.get(hwnd)
        if info is None or self._editing_hwnd is not None:
            return
        self._editing_hwnd = hwnd

        row = info['canvas']
        title_item = info['title_item']
        display_title = info['raw_title']
        current = self._get_nickname(hwnd, display_title) or display_title
        row.itemconfigure(title_item, state='hidden')

        entry = tk.Entry(row, font=self._font, bg=cfg.BUTTON_BG, fg=cfg.FG_COLOR,
                         insertbackground=cfg.FG_COLOR, relief='flat',
                         selectbackground=cfg.ACCENT_COLOR)
        entry.insert(0, current)
        entry.select_range(0, 'end')
        entry_id = row.create_window(info['title_x'], cfg.ROW_HEIGHT // 2, window=entry,
                                     anchor='w', width=info['title_w'],
                                     height=cfg.ROW_HEIGHT - 8)
        entry.focus_set()

        def close_entry():
            row.delete(entry_id)
            entry.destroy()
            row.itemconfigure(title_item, state='normal')
            self._editing_hwnd = None

        def finish(event=None):
            new_name = entry.get().strip()
            close_entry()

            if new_name and new_name != display_title:
                self._nicknames[hwnd] = (new_name, display_title)
                shown = new_name
            else:
                # Clear nickname
                self._nicknames.pop(hwnd, None)
                shown = display_title
            row.itemconfigure(title_item,
                              text=_fit_text(shown, info['title_font'], info['title_w']))

        def cancel(event=None):
            close_entry()

        entry.bind('<Return>', finish)
        entry.bind('<Escape>', cancel)
        entry.bind('<FocusOut>', finish)
