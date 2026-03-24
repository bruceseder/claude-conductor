# Claude Conductor

A Windows 11 desktop widget for managing multiple Claude Code CLI terminal windows. See all your sessions at a glance, know which ones need your attention, and arrange them across your monitors with one click.

![Windows 11](https://img.shields.io/badge/Windows%2011-0078D6?logo=windows11&logoColor=white)
![Python 3.12+](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)

## The Problem

When you run multiple Claude Code CLI sessions simultaneously, it's hard to know which ones are waiting for you and which ones are still working. You end up cycling through windows trying to find the one that needs a yes/no answer or has finished its task.

## The Solution

Claude Conductor is a compact, always-on-top floating widget that:

- **Lists all your Claude windows** in one place
- **Pulses orange** when Claude needs a decision from you (yes/no, 1/2/3 choices, tool approvals)
- **Pulses teal-green** when Claude is done and waiting for your next instruction
- **Pulses the actual window border** so you can spot which window needs you even without the widget
- **Tiles and arranges** windows across multiple monitors with one click

## Features

### Attention Pulse System

The signature feature. Claude Conductor reads the terminal text of each window using the Windows UI Automation API to determine exactly what state each session is in:

| State | Widget Color | Border Color | Meaning |
|-------|-------------|-------------|---------|
| Working | No pulse | Default | Claude is actively processing (spinner in title) |
| Needs decision | Orange pulse | Orange pulse | Claude is asking yes/no, 1/2/3, or needs tool approval |
| Done / idle | Teal-green pulse | Teal-green pulse | Claude finished, waiting for your next instruction |

The detection works by:
- **Spinner detection**: Braille characters (U+2800-U+28FF) in the window title mean Claude is actively working. The static sparkle (U+2733) means Claude is present but idle.
- **Terminal text analysis**: Reads the terminal buffer via UI Automation and looks for patterns like "Esc to cancel" (choice UI), bare `>` prompt (idle), or `●` (done).
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

psutil is listed in requirements.txt but not currently used.

## Installation

```bash
git clone https://github.com/bruceseder/claude-conductor.git
cd claude-conductor
pip install pywin32 comtypes
```

## Usage

```bash
python main.py
```

The widget appears in the bottom-right corner of your primary monitor. Open some Claude Code CLI sessions and they'll appear in the list automatically.

## How It Works

### Window Detection

Finds windows by Win32 class name (`CASCADIA_HOSTING_WINDOW_CLASS` for Windows Terminal, `ConsoleWindowClass` for legacy console) and by title keywords (`claude`, `powershell`, `pwsh`, `bash`, `cmd`). Also detects the Claude desktop app (`Chrome_WidgetWin_1`).

### Terminal Text Reading

Uses the Windows UI Automation COM API to walk the accessibility tree of each terminal window, find the `TermControl` element, and read its `TextPattern` content. This gives access to the full terminal buffer (up to 200KB) to detect what state Claude is in.

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

Early release — actively being developed. Core features work but edge cases in attention detection are still being refined.

## License

MIT
