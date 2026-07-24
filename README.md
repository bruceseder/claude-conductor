# Claude Conductor

A Windows 11 desktop widget for managing multiple Claude Code CLI terminal windows. See all your sessions at a glance, know which ones need your attention, and arrange them across your monitors with one click.

![Windows 11](https://img.shields.io/badge/Windows%2011-0078D6?logo=windows11&logoColor=white)
![Python 3.12+](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)

## The Problem

When you run multiple Claude Code CLI sessions simultaneously, it's hard to know which ones are waiting for you and which ones are still working. You end up cycling through windows trying to find the one that needs a yes/no answer or has finished its task.

## The Solution

Claude Conductor is a compact, always-on-top floating widget that:

- **Lists all your Claude windows** in one place (non-Claude terminals are filtered out)
- **Pulses orange** when Claude needs a decision from you (yes/no, 1/2/3 choices, tool approvals)
- **Pulses blue** while Claude is actively working
- **Pulses green** when Claude is done and waiting for your next instruction
- **Pulses the actual window border** so you can spot which window needs you even without the widget
- **Tiles and arranges** windows across multiple monitors with one click
- **Tracks time per project** so you know how much Claude is working on each codebase

## Features

### Attention Pulse System

The signature feature. Claude Conductor reads the terminal text of each window using the Windows UI Automation API to determine exactly what state each session is in:

| State | Widget Color | Border Color | Meaning |
|-------|-------------|-------------|---------|
| Needs decision | Orange pulse | Orange pulse | Claude is asking yes/no, 1/2/3, or needs tool approval |
| Working | Blue pulse | Blue pulse | Claude is actively processing a request or tool call |
| Done / idle | Green pulse | Green pulse | Claude finished, waiting for your next instruction |

The detection works by:
- **Terminal text analysis**: Reads the terminal buffer via UI Automation and matches footer patterns — `Esc to cancel` (a choice is on screen), `Esc to interrupt` (actively working), or a bare `>` prompt / trailing `●` (idle). Choice wins over working, and working over idle, so a session asking a question never reads as merely busy.
- **Why not the title spinner**: the braille spinner (U+2800-U+28FF) in the window title is an unreliable working signal — it can disappear during tool calls — so the terminal footer is authoritative.
- **TUI prompt detection**: When the terminal text doesn't match known patterns but Claude has stopped working, it defaults to orange (likely a permission/tool approval prompt rendered as a TUI overlay).

Window borders pulse using the Windows 11 DWM API (`DwmSetWindowAttribute` with `DWMWA_BORDER_COLOR`), throttled to avoid flicker.

### Window Management

- **Click to focus**: Click any window in the list to bring it to the foreground (uses `AttachThreadInput` workaround for Windows focus restrictions)
- **Minimize all / Restore all**: Quick buttons to hide or show all Claude windows
- **Auto-refresh**: Scans for new and closed windows every 2 seconds

### Tiling Layouts

Arrange all detected windows with one click:

| Layout | Description |
|--------|-------------|
| Grid | Optimal rows/columns based on window count |
| H-Split | Side by side (falls back to grid if > 4) |
| V-Split | Stacked top to bottom (falls back to grid if > 4) |
| Cascade | Overlapping with offset |

### Multi-Monitor Support

- **All monitors**: Tile across the combined work area of all displays
- **Specific monitor**: Tile on a single selected monitor
- **Distribute**: Spread windows evenly across monitors (round-robin assignment, then tile each group)

### Time Tracking

Claude Conductor tracks how much time Claude spends working on each project. Projects are identified by their **working directory** (resolved via the process tree using `psutil`), so renaming windows doesn't split accumulated time.

Time is accumulated from three sources:
- **Thinking time** (purple/spinning): real seconds while Claude is actively processing
- **Idle transition** (green): +5 minutes each time Claude finishes, accounting for your time reading output and typing the next prompt
- **Choice transition** (orange): +1 minute each time Claude asks a question, accounting for decision time

Time is displayed per-row in the widget and persisted to `claude_time.json`, keyed by `project_directory|date`. Multiple windows in the same project directory accumulate into the same bucket.

### Usage Stats

A compact row of live gauges mirrors Claude Code's `/usage` view so you can track your limits without leaving your work:

- **Session** (5-hour rolling window), **Week** (all models), and **Fable** (the weekly limit for the most-capable model) — each a mini-gauge that fills green → amber → red as you approach the limit, showing the percent plus a thin red **pace marker** for where your usage "should" be at even (wall-clock) consumption.
- **Extra** — an extra-usage credits gauge in the bottom status row (beside the Code / API / Web service indicators) showing `$used / $cap`.

The numbers come from Anthropic's OAuth usage endpoint, authenticated with the token Claude Code already stores (read fresh each poll, so token refreshes are picked up automatically). That subscription endpoint is meant for *sparing* checks — frequent polling is rate-limited and can consume a small percentage of your 5-hour session limit — so the widget polls once every **15 minutes**, backs off exponentially on a rate-limit (429) response, and keeps the last-good values rather than blanking on a transient failure.

### Widget UI

- Dark theme matching terminal aesthetics (Catppuccin Mocha inspired)
- Custom title bar with drag-to-move
- Pin/unpin always-on-top
- Minimize to a small restore tab on the screen edge
- Resize grip for adjustable height
- Scrollable window list for many sessions

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+1 through Ctrl+9 | Focus the Nth window in the list |
| Ctrl+G | Grid tile |
| Ctrl+H | Horizontal tile |
| Ctrl+J | Vertical tile |
| Escape | Minimize widget |

## Requirements

- **Windows 11** (uses DWM border color API; window detection works on Windows 10 but border pulsing won't)
- **Python 3.12+**
- **pywin32** (`pip install pywin32`)
- **comtypes** (`pip install comtypes`) — for UI Automation terminal text reading
- **psutil** (`pip install psutil`) — for resolving project working directories from process trees

## Installation

```bash
git clone https://github.com/bruceseder/claude-conductor.git
cd claude-conductor
pip install pywin32 comtypes psutil
```

## Usage

```bash
python main.py
```

The widget appears in the bottom-right corner of your primary monitor. Open some Claude Code CLI sessions and they'll appear in the list automatically.

## How It Works

### Window Detection

Finds windows by Win32 class name (`CASCADIA_HOSTING_WINDOW_CLASS` for Windows Terminal, `ConsoleWindowClass` for legacy console) and by title keywords. Only Claude Code sessions are shown — plain terminal windows, File Explorer, and browsers are filtered out. Windows are identified by their HWND (window handle), not by title, so renaming via Claude's `/rename` command is immediately reflected without breaking the connection.

### Terminal Text Reading

Uses the Windows UI Automation COM API to walk the accessibility tree of each terminal window, find the `TermControl` element, and read its `TextPattern` content to detect what state Claude is in. Windows Terminal exposes roughly the on-screen viewport (~30 lines) through this API rather than the full scrollback, so the read is cheap (sub-millisecond) regardless of session length. The `TermControl` element is cached per window to skip re-walking the tree on every poll.

### DWM Border Pulsing

Calls `DwmSetWindowAttribute` with `DWMWA_BORDER_COLOR` (attribute 34) to set the window frame color. Colors are interpolated using a sine wave for smooth pulsing. Updates are throttled to every ~300ms to avoid flicker on Electron-based windows.

## Project Structure

```
claude-conductor/
├── main.py                  # Entry point (DPI awareness + bootstrap)
├── widget/
│   ├── app.py               # Application orchestration
│   ├── ui.py                # Tkinter widget UI and pulse animation
│   ├── window_manager.py    # Win32 window enumeration and manipulation
│   ├── monitor_manager.py   # Multi-monitor detection and work areas
│   ├── terminal_reader.py   # UI Automation terminal text reading
│   ├── time_tracker.py      # Per-project time tracking (by working directory)
│   ├── tiling.py            # Layout algorithms (grid, h-split, v-split, cascade)
│   ├── config.py            # Constants, colors, detection patterns
│   └── utils.py             # Win32 helpers, DPI, color lerp, DWM border API
├── requirements.txt
└── .gitignore
```

## Known Issues

- Choice detection can occasionally show orange for stale prompts still visible in the terminal scrollback
- Some TUI prompts (tool approvals, permission requests) render as overlays that aren't captured by the UI Automation text buffer — these default to orange which is the safer assumption
- The Claude desktop app (Electron) window can't have its terminal text read, so it always shows as idle when not working
- Border pulsing may have slight jumps on Electron-based windows due to how they handle DWM attribute changes

## Status

**v0.9.0** — in daily use and stable, but pre-1.0 while edge cases in attention detection are still being refined (see Known Issues). See the [changelog](CHANGELOG.md) for what changed.

## License

MIT
