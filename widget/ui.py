import math
import tkinter as tk
from tkinter import font as tkfont

from . import config as cfg
from .utils import lerp_color, set_window_border_color, reset_window_border_color


class PowerWidget(tk.Toplevel):
    def __init__(self, master, monitors, on_focus, on_tile, on_minimize_all,
                 on_restore_all, on_refresh, on_monitor_change):
        super().__init__(master)

        self._on_focus = on_focus
        self._on_tile = on_tile
        self._on_minimize_all = on_minimize_all
        self._on_restore_all = on_restore_all
        self._on_refresh = on_refresh
        self._on_monitor_change = on_monitor_change

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

        self._setup_window()
        self._setup_fonts()
        self._build_ui()

    def _setup_window(self):
        self.overrideredirect(True)
        self.attributes('-topmost', True)
        self.attributes('-alpha', 0.95)
        self.configure(bg=cfg.BG_COLOR)
        self.geometry(f'{cfg.WIDGET_WIDTH}x{cfg.WIDGET_MIN_HEIGHT}')

        # Position bottom-right of primary monitor
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = screen_w - cfg.WIDGET_WIDTH - 20
        y = screen_h - cfg.WIDGET_MIN_HEIGHT - 80
        self.geometry(f'+{x}+{y}')

        self.minsize(cfg.WIDGET_WIDTH, cfg.WIDGET_MIN_HEIGHT)
        self.maxsize(cfg.WIDGET_WIDTH, cfg.WIDGET_MAX_HEIGHT)

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
        # Window list
        self._build_window_list()
        # Status bar
        self._build_status_bar()

    # --- Title Bar ---
    def _build_title_bar(self):
        bar = tk.Frame(self, bg=cfg.BG_SECONDARY, height=30)
        bar.pack(fill='x')
        bar.pack_propagate(False)

        # Drag handling
        bar.bind('<Button-1>', self._start_drag)
        bar.bind('<B1-Motion>', self._on_drag)

        # Icon + title
        lbl = tk.Label(bar, text=" \u26A1 Power Widget", font=self._font_bold,
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
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.geometry(f'+{x}+{y}')

    def _toggle_pin(self):
        self._pinned = not self._pinned
        self.attributes('-topmost', self._pinned)
        self._pin_btn.configure(fg=cfg.ACCENT_COLOR if self._pinned else cfg.FG_DIM)

    def _on_close(self):
        self.master.destroy()

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
        self._restore_tab.overrideredirect(True)
        self._restore_tab.attributes('-topmost', True)
        self._restore_tab.attributes('-alpha', 0.92)
        self._restore_tab.configure(bg=cfg.ACCENT_COLOR)

        # Position on right edge of primary monitor, vertically centered
        screen_w = self.master.winfo_screenwidth()
        screen_h = self.master.winfo_screenheight()
        tab_w, tab_h = 40, 100
        self._restore_tab.geometry(f'{tab_w}x{tab_h}+{screen_w - tab_w}+{screen_h // 2 - tab_h // 2}')

        lbl = tk.Label(self._restore_tab, text="\u26A1\nPW", font=self._font_bold,
                       bg=cfg.ACCENT_COLOR, fg=cfg.BG_COLOR, cursor='hand2',
                       justify='center')
        lbl.pack(fill='both', expand=True)
        lbl.bind('<Button-1>', lambda e: self._restore_widget())
        self._restore_tab.bind('<Button-1>', lambda e: self._restore_widget())

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

        self._status_label = tk.Label(self._status_frame, text="0 windows",
                                       font=self._font_small, bg=cfg.BG_SECONDARY,
                                       fg=cfg.FG_DIM, anchor='w')
        self._status_label.pack(side='left', padx=8, fill='y')

        # Resize grip
        grip = tk.Label(self._status_frame, text="\u2261", font=self._font_icon,
                        bg=cfg.BG_SECONDARY, fg=cfg.FG_DIM, cursor='sb_v_double_arrow')
        grip.pack(side='right', padx=4)
        grip.bind('<Button-1>', self._start_resize)
        grip.bind('<B1-Motion>', self._on_resize)

    def _start_resize(self, event):
        self._resize_y = event.y_root
        self._resize_h = self.winfo_height()

    def _on_resize(self, event):
        dy = event.y_root - self._resize_y
        new_h = max(cfg.WIDGET_MIN_HEIGHT, min(cfg.WIDGET_MAX_HEIGHT, self._resize_h + dy))
        self.geometry(f'{cfg.WIDGET_WIDTH}x{new_h}')

    # --- Public API ---
    def update_window_list(self, windows):
        """Rebuild the window list with current windows."""
        # Clear existing rows
        for w in self._inner_frame.winfo_children():
            w.destroy()

        self._window_rows = []
        new_attention_hwnds = {w.hwnd for w in windows if w.needs_attention}

        # Reset borders for windows that no longer need attention
        stale = self._border_pulsing - new_attention_hwnds
        for hwnd in stale:
            reset_window_border_color(hwnd)
        self._border_pulsing -= stale
        # Clean up stale color cache
        for hwnd in list(self._last_border_color):
            if hwnd not in new_attention_hwnds:
                del self._last_border_color[hwnd]

        self._pulse_rows = {}

        for i, win in enumerate(windows):
            row = tk.Frame(self._inner_frame, bg=cfg.BG_COLOR, height=cfg.ROW_HEIGHT)
            row.pack(fill='x', pady=1)
            row.pack_propagate(False)

            # Color based on attention type
            if win.needs_attention and win.attention_type == 'choice':
                base_color = cfg.ATTENTION_COLOR
            elif win.needs_attention:
                base_color = cfg.IDLE_COLOR
            elif win.is_claude:
                base_color = cfg.CLAUDE_COLOR
            else:
                base_color = cfg.FG_DIM

            dot = tk.Canvas(row, width=12, height=12, bg=cfg.BG_COLOR,
                           highlightthickness=0)
            dot.pack(side='left', padx=(8, 4), pady=0)
            dot_oval = dot.create_oval(2, 2, 10, 10, fill=base_color, outline='')

            # Title
            title = win.display_title or win.title
            if len(title) > 38:
                title = title[:36] + "\u2026"

            fg = base_color if win.needs_attention else cfg.FG_COLOR
            lbl = tk.Label(row, text=title, font=self._font_bold if win.needs_attention else self._font,
                          bg=cfg.BG_COLOR, fg=fg, anchor='w')
            lbl.pack(side='left', fill='x', expand=True, padx=(0, 4))

            # Attention indicator with type hint
            if win.needs_attention:
                indicator = "\u2753" if win.attention_type == 'choice' else "\u2713"  # ? vs checkmark
                attn_lbl = tk.Label(row, text=indicator, font=self._font_small,
                                    bg=cfg.BG_COLOR, fg=base_color)
                attn_lbl.pack(side='right', padx=2)

            # Minimized indicator
            if win.is_minimized:
                min_lbl = tk.Label(row, text="\u2500", font=self._font_small,
                                   bg=cfg.BG_COLOR, fg=cfg.FG_DIM)
                min_lbl.pack(side='right', padx=4)

            # Number shortcut label
            if i < 9:
                num_lbl = tk.Label(row, text=str(i + 1), font=self._font_small,
                                   bg=cfg.BG_COLOR, fg=cfg.FG_DIM)
                num_lbl.pack(side='right', padx=(0, 6))

            # Bind click to focus
            hwnd = win.hwnd
            for widget in [row, lbl, dot]:
                widget.bind('<Button-1>', lambda e, h=hwnd: self._on_focus(h))

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
                self._pulse_rows[win.hwnd] = (row, lbl, dot, dot_oval, win.attention_type)

            self._window_rows.append((win.hwnd, row))

        # Start or stop pulse animation
        if self._pulse_rows and not self._pulse_running:
            self._pulse_running = True
            self._animate_pulse()
        elif not self._pulse_rows:
            self._pulse_running = False

        # Update status
        total = len(windows)
        claude_count = sum(1 for w in windows if w.is_claude)
        attention_count = sum(1 for w in windows if w.needs_attention)
        status = f"{total} window{'s' if total != 1 else ''}"
        if claude_count > 0:
            status += f" ({claude_count} Claude)"
        if attention_count > 0:
            status += f" \u2022 {attention_count} waiting"
        self._status_label.configure(text=status)

        # Auto-resize height based on content
        desired_h = 130 + len(windows) * (cfg.ROW_HEIGHT + 1)
        desired_h = max(cfg.WIDGET_MIN_HEIGHT, min(cfg.WIDGET_MAX_HEIGHT, desired_h))
        current_h = self.winfo_height()
        if abs(desired_h - current_h) > 20:
            self.geometry(f'{cfg.WIDGET_WIDTH}x{desired_h}')

    def _animate_pulse(self):
        """Smooth pulsating glow for attention rows AND actual window borders."""
        if not self._pulse_running or not self._pulse_rows:
            self._pulse_running = False
            # Reset any lingering border colors
            for hwnd in list(self._border_pulsing):
                reset_window_border_color(hwnd)
            self._border_pulsing.clear()
            return

        self._pulse_phase += cfg.PULSE_SPEED
        # Sine wave: 0..1..0 smoothly
        t = (math.sin(self._pulse_phase) + 1.0) / 2.0

        # Only update DWM borders every 6th frame (~300ms) to avoid flicker
        self._border_frame_count += 1
        update_borders = (self._border_frame_count % 6) == 0

        for hwnd, (row, lbl, dot_canvas, dot_oval, atype) in self._pulse_rows.items():
            # Pick color scheme based on attention type
            if atype == 'choice':
                color_main = cfg.ATTENTION_COLOR
                color_bright = cfg.ATTENTION_COLOR_BRIGHT
                color_dim = cfg.ATTENTION_COLOR_DIM
            else:  # idle/done
                color_main = cfg.IDLE_COLOR
                color_bright = cfg.IDLE_COLOR_BRIGHT
                color_dim = cfg.IDLE_COLOR_DIM

            bg = lerp_color(cfg.BG_COLOR, color_dim, t)
            dot_c = lerp_color(color_main, color_bright, t)
            fg = lerp_color(color_main, color_bright, t)
            border_color = lerp_color(color_dim, color_main, t)

            try:
                row.configure(bg=bg)
                lbl.configure(bg=bg, fg=fg)
                dot_canvas.configure(bg=bg)
                dot_canvas.itemconfigure(dot_oval, fill=dot_c)
                for child in row.winfo_children():
                    if isinstance(child, tk.Label):
                        child.configure(bg=bg)
            except tk.TclError:
                pass

            # Pulse the actual window border via DWM (throttled)
            if update_borders:
                last = self._last_border_color.get(hwnd)
                if last != border_color:
                    set_window_border_color(hwnd, border_color)
                    self._last_border_color[hwnd] = border_color
                self._border_pulsing.add(hwnd)

        self.after(cfg.PULSE_INTERVAL_MS, self._animate_pulse)

    def update_monitors(self, monitors):
        """Update the monitor selector options."""
        self._monitors = monitors
        self._rebuild_monitor_menu()

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
