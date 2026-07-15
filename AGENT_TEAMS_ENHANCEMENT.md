# Claude Conductor: Agent Teams Enhancement Plan

## Overview

Enhance Claude Conductor from a **window observer/manager** into a **session orchestrator** that supports Claude Code's experimental Agent Teams feature on Windows. The core idea: Conductor intercepts tmux commands from Claude Code via a shim, translates them into Windows Terminal split-pane operations within a single contained window, and continues to provide attention-state monitoring across all panes.

### What This Achieves

The macOS app **cmux** (cmux.com) provides a native terminal with auto-splitting panes for Claude Code agent teams. This enhancement gives Claude Conductor equivalent functionality on Windows, using Windows Terminal as the container and the existing Conductor widget as the control plane.

### Design Principles

- **One window, many panes.** Agent teammates spawn as split panes inside a single Windows Terminal window, not as separate floating windows. No desktop clutter.
- **Conductor stays external.** The widget remains a separate overlay that observes and manages -- it does not become a terminal emulator.
- **Real terminals.** Every pane is a native Windows Terminal pane with full GPU rendering, scrollback, and color support. No xterm.js re-rendering.
- **Backward compatible.** Existing multi-window mode for independent sessions still works. Agent Teams mode is additive.

### Key Research Findings (from spike testing)

These findings informed the architecture decisions below:

1. **The tmux shim is the only viable path.** Claude Code's `teammateMode: "in-process"` runs teammates as invisible async tasks in the same Node.js process — no windows, no panes, nothing to observe. On Windows, `"auto"` mode always falls back to in-process because tmux is unavailable. The shim is required to unlock visible teammate panes.

2. **`tmux` is hardcoded.** Claude Code's `cli.js` line 2176: `dZ="tmux"`. No config to override. PATH manipulation is the only option.

3. **`wt.exe -w <name> split-pane` works from external processes.** Spike tested: named windows (`-w`) reliably target the correct WT window. Splits land as panes, not new tabs (fixed in WT 1.23+).

4. **Claude Code uses `-L` for socket isolation.** All TmuxBackend calls include `-L claude-swarm-<pid>`. The shim must accept and ignore this flag.

5. **Two conditions for tmux mode on Windows:** (a) `tmux -V` must succeed, (b) `TMUX` env var must be set in the lead session.

6. **No WT programmatic API exists.** `wt.exe` CLI is the only external control mechanism — no COM, no REST, no URI scheme.

---

## Architecture

### Current State

```
┌─────────────────────┐
│  Conductor Widget    │  ← Discovers windows, reads terminal text,
│  (Python/tkinter)    │     pulses borders, manages tiling
└─────────┬───────────┘
          │ observes via Win32 + UI Automation
          ▼
┌──────┐ ┌──────┐ ┌──────┐
│ WT 1 │ │ WT 2 │ │ WT 3 │  ← Independent Windows Terminal windows
└──────┘ └──────┘ └──────┘     (user opens manually)
```

### Target State

```
┌─────────────────────┐
│  Conductor Widget    │  ← Same widget, now also receives shim messages
│  (Python/tkinter)    │     and spawns/kills panes in the container
└──┬──────────┬───────┘
   │          │
   │ named    │ observes via Win32 + UI Automation
   │ pipe     │
   │          ▼
   │  ┌─────────────────────────────────────────┐
   │  │  Windows Terminal (Container Window)      │
   │  │ ┌───────────────────┬───────────────────┐ │
   │  │ │                   │  Teammate 1 (UX)  │ │
   │  │ │                   │  ● Working...      │ │
   │  │ │  Lead Agent       ├───────────────────┤ │
   │  │ │  (main session)   │  Teammate 2 (Arch)│ │
   │  │ │                   │  ⏳ Needs input    │ │
   │  │ │  You type here    ├───────────────────┤ │
   │  │ │                   │  Teammate 3 (QA)  │ │
   │  │ │                   │  ✓ Done            │ │
   │  │ └───────────────────┴───────────────────┘ │
   │  └─────────────────────────────────────────┘
   │
   │ ┌──────────────┐
   └─│  tmux shim   │  ← Claude Code calls this thinking it's tmux
     │  (tmux.exe)  │     Shim sends JSON to Conductor via named pipe
     └──────────────┘
```

---

## Component Design

### Component 1: tmux Shim (`shim/tmux.py` → compiled to `tmux.exe`)

A small Python script (compiled to `tmux.exe` via PyInstaller and placed on PATH) that Claude Code invokes when it thinks it's using tmux. The shim either handles the command locally (version check) or forwards it to Conductor over a named pipe.

#### Command Dispatch Table

Claude Code's `TmuxBackend` uses exactly these commands. Note: most calls include `-L claude-swarm-<pid>` for socket isolation — the shim must accept and ignore this flag.

| Claude Code Calls | Shim Behavior | Response |
|---|---|---|
| `tmux -V` | Handle locally | Returns `tmux 3.4` (fake version string) |
| `tmux [-L <socket>] has-session -t <name>` | Send to Conductor | Exit code 0 if Conductor is running with active session, 1 otherwise |
| `tmux [-L <socket>] new-session -d -s <name> [-x W] [-y H]` | Send to Conductor | Creates a new team session, returns session name |
| `tmux [-L <socket>] split-window -h -c <dir> [-F <fmt>]` | Send to Conductor | Conductor opens new WT pane, returns pane ID in requested format |
| `tmux [-L <socket>] split-window -v -c <dir> [-F <fmt>]` | Send to Conductor | Same, but vertical split |
| `tmux [-L <socket>] send-keys -t <pane> <text> [Enter]` | Send to Conductor | Conductor writes to pane's named pipe relay |
| `tmux [-L <socket>] kill-pane -t <pane>` | Send to Conductor | Conductor closes target pane |
| `tmux [-L <socket>] list-panes -F <fmt>` | Send to Conductor | Returns pane list in tmux format string |
| `tmux [-L <socket>] display-message -p -F <fmt>` | Send to Conductor | Returns session/pane metadata |
| `tmux [-L <socket>] list-windows [-F <fmt>]` | Send to Conductor | Returns window list (single window) |
| `tmux [-L <socket>] attach-session -t <name>` | Send to Conductor | No-op success (session is already visible in WT container) |

#### Format String Support

Claude Code uses tmux format strings like `#{pane_id}`, `#{pane_pid}`, `#{pane_current_path}`. The shim/Conductor must translate these. Key variables to support:

- `#{pane_id}` → `%0`, `%1`, `%2`, etc.
- `#{pane_pid}` → PID of the shell process in that pane
- `#{pane_current_path}` → Working directory of the pane
- `#{window_id}` → Always `@0` (single window)
- `#{session_name}` → The session name from Conductor

#### Environment Variables

The shim must set (or Conductor must ensure are set in the lead session's environment):

```
TMUX=/tmp/conductor-shim-{pid},0,0
TMUX_PANE=%0
CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
```

Claude Code checks `process.env.TMUX` to determine it's inside a tmux-compatible session. The value format is `socket_path,pid,window_index`.

#### Shim Implementation Notes

- The shim must be a **blocking CLI tool** — Claude Code invokes it synchronously (via `spawnSync`) and reads stdout/stderr + exit code.
- Communication to Conductor is over a **named pipe** (`\\.\pipe\claude-conductor`). The shim sends a JSON request, blocks until Conductor responds with a JSON result, then prints the expected tmux output to stdout.
- The shim must parse tmux command-line arguments accurately. Use Python's `argparse` or manual parsing since tmux CLI syntax has some quirks (e.g., `-t` can be `tmux kill-pane -t %3` or `tmux kill-pane -t%3`).
- The `-L <socket>` flag appears on most calls. The shim must accept it (to avoid parse errors) but ignores it — we use a single named pipe, not per-session Unix sockets.
- Compile with PyInstaller: `pyinstaller --onefile --name tmux shim/tmux.py`.
- **PATH setup:** Conductor prepends the shim directory to PATH when launching the lead session's terminal pane. This ensures `tmux.exe` resolves to the shim within Conductor-managed sessions only, without affecting the system PATH or other terminals.

#### Shim File

```
shim/
├── tmux.py          # Main shim script
├── build_shim.py    # PyInstaller build script
└── README.md        # Installation instructions
```

---

### Component 2: Shim Server (`widget/shim_server.py`)

A named pipe server running in Conductor's process that listens for messages from the tmux shim.

#### Protocol

- **Transport:** Windows Named Pipe (`\\.\pipe\claude-conductor`)
- **Format:** Newline-delimited JSON
- **Flow:** Shim connects → sends JSON request → Conductor processes → sends JSON response → shim disconnects

#### Request Schema

```json
{
  "cmd": "split-window",
  "args": {
    "horizontal": true,
    "directory": "C:\\Users\\bruce\\project",
    "format": "#{pane_id}"
  }
}
```

#### Response Schema

```json
{
  "ok": true,
  "stdout": "%1",
  "stderr": "",
  "exit_code": 0
}
```

#### Threading

The named pipe server runs in a **daemon thread** within the Conductor process. It uses `win32pipe` and `win32file` from pywin32 (already a dependency). Each incoming connection is handled synchronously since tmux commands from Claude Code are sequential per-session.

---

### Component 3: Session Manager (`widget/session_manager.py`)

Tracks the lifecycle of agent team sessions: which Windows Terminal window is the container, which panes exist, parent-child relationships, and pane metadata.

#### Data Model

```python
@dataclass
class PaneInfo:
    pane_id: str           # "%0", "%1", etc.
    process_pid: int       # PID of shell process in pane
    working_dir: str       # Starting directory
    role: str              # "lead" or "teammate"
    agent_id: str | None   # Claude Code's agent ID (from send-keys command parsing)
    agent_name: str | None # Claude Code's agent name
    hwnd: int | None       # Window handle for the TermControl (for UI Automation)
    created_at: float      # timestamp

@dataclass  
class TeamSession:
    session_name: str
    container_hwnd: int          # The Windows Terminal window handle
    panes: dict[str, PaneInfo]   # pane_id → PaneInfo
    next_pane_id: int            # Counter for generating %0, %1, %2...
    lead_pane_id: str            # The pane ID of the lead agent
```

#### Key Methods

```python
class SessionManager:
    def create_session(self, name: str) -> TeamSession
    def spawn_pane(self, session: str, direction: str, working_dir: str) -> PaneInfo
    def kill_pane(self, session: str, pane_id: str) -> bool
    def list_panes(self, session: str) -> list[PaneInfo]
    def get_pane(self, session: str, pane_id: str) -> PaneInfo | None
    def has_session(self, name: str) -> bool
    def resolve_pane_hwnd(self, pane: PaneInfo) -> int | None  # UI Automation lookup
```

#### Pane HWND Resolution (Cached + Invalidated)

After spawning a pane via `wt.exe split-pane`, the Session Manager needs to find the new pane's TermControl HWND for terminal text reading. Strategy:

1. Before spawning, enumerate existing TermControl elements in the container window.
2. Spawn the pane via `wt.exe`.
3. After a short delay (~500ms), re-enumerate TermControl elements.
4. The new element is the one that wasn't there before.
5. **Cache** its HWND in the PaneInfo.

HWNDs are cached for fast per-pane attention polling. The cache is **invalidated and re-enumerated** only when the session manager detects a pane lifecycle event (create or kill). This avoids expensive UI Automation tree walks on every poll cycle while self-healing when WT rebuilds its accessibility tree after a pane close.

This leverages the existing `terminal_reader.py` UI Automation infrastructure.

---

### Component 4: Windows Terminal Integration (`widget/wt_integration.py`)

Handles all interactions with Windows Terminal: launching the container, splitting panes, and sending keystrokes to specific panes.

#### Launching the Container

The container is launched **lazily** on the first `split-window` command (no explicit "Start Agent Team" button required). Conductor uses WT's named window feature (`-w`) to reliably target the container for subsequent operations.

```python
def launch_container(working_dir: str, session_name: str) -> int:
    """Launch a named Windows Terminal window and return its HWND."""
    window_name = f"claude-team-{session_name}"
    cmd = [
        "wt.exe",
        "-w", window_name,
        "new-tab",
        "--title", f"Claude Team: {session_name}",
        "-d", working_dir,
    ]
    subprocess.Popen(cmd)
    # Wait for window to appear, find HWND by title match
    # Set environment: TMUX, TMUX_PANE, CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS
    return hwnd
```

All subsequent `split-pane` calls use `-w <window_name>` to target the correct container, avoiding ambiguity with other WT windows.

#### Splitting Panes

```python
def split_pane(session: TeamSession, direction: str, working_dir: str, pane_id: str) -> None:
    """Split the focused pane in the named container window.
    
    Each pane runs pane_relay.py which creates a per-pane named pipe
    for keystroke injection and launches the shell as a child process.
    """
    relay_cmd = f"python pane_relay.py --pane-id {pane_id} --pipe-name claude-conductor-pane-{pane_id}"
    args = [
        "wt.exe", "-w", session.window_name, "split-pane",
    ]
    if direction == "horizontal":
        args.append("-V")   # WT -V = vertical split line = panes side by side = tmux -h
    else:
        args.append("-H")   # WT -H = horizontal split line = panes stacked = tmux -v
    args.extend(["-d", working_dir, "--title", f"Pane {pane_id}"])
    args.extend(["cmd", "/k", relay_cmd])
    subprocess.Popen(args)
```

**Important:** Windows Terminal and tmux use opposite terminology:
- tmux `-h` (horizontal split) = panes side by side = WT `-V` (vertical split line)
- tmux `-v` (vertical split) = panes stacked = WT `-H` (horizontal split line)

The `-w <window_name>` flag ensures we always target the correct container, even if multiple WT windows are open.

#### Sending Keystrokes to a Specific Pane

`wt.exe` has no direct "send text to pane X" command. We use **per-pane named pipe helpers** for deterministic, race-free keystroke injection.

**Approach: Named pipe per pane**
1. When spawning each pane, the pane's process is a thin relay script (`shim/pane_relay.py`) that:
   - Creates a per-pane named pipe (`\\.\pipe\claude-conductor-pane-{pane_id}`)
   - Reads text commands from the pipe
   - Forwards them to stdin of a child process (the shell or Claude Code)
   - Reports child process exit back to Conductor
2. Conductor writes to the appropriate pipe to inject keystrokes for any `send-keys` command.
3. Each relay also reports its child's PID back to Conductor on startup (for process tree tracking).

**Why not focus-and-type:** Multiple teammate spawns can fire rapidly, causing race conditions with focus switching. Named pipes are deterministic and don't require window focus changes.

**Why not WriteConsoleInput:** Windows Terminal uses ConPTY, not traditional consoles. `AttachConsole` doesn't work reliably with ConPTY-backed panes.

#### Layout Strategy

Agent team panes should follow cmux's layout: **lead on the left half, teammates stacked vertically on the right half.**

Achieving this with `wt.exe split-pane`:
1. Container starts with the lead pane (full window).
2. First teammate: `wt.exe split-pane --horizontal -d <dir>` → splits into left (lead) and right (teammate 1).
3. Second teammate: Focus teammate 1's pane, then `wt.exe split-pane --vertical -d <dir>` → teammate 1 and 2 stack vertically on the right.
4. Third teammate: Focus the last teammate pane, `wt.exe split-pane --vertical -d <dir>` → three teammates stacked on the right.

This produces exactly:
```
┌──────────────────┬──────────────┐
│                  │ Teammate 1   │
│                  ├──────────────┤
│  Lead Agent      │ Teammate 2   │
│                  ├──────────────┤
│                  │ Teammate 3   │
└──────────────────┴──────────────┘
```

Windows Terminal auto-equalizes pane sizes within a split direction, so teammates will share the right column equally -- same behavior as cmux.

When a teammate pane closes (its process exits), WT automatically removes it and rebalances the remaining panes.

---

### Component 5: Enhanced Widget UI (`widget/ui.py` modifications)

#### New UI Elements

1. **Session grouping in the window list.** When an agent team session is active (launched lazily on first `split-window`), panes are grouped under a collapsible "Team: <name>" header. Each pane shows:
   - Role icon (crown for lead, person for teammate)
   - Agent name (parsed from the `claude.exe --agent-name` argument)
   - Attention state (existing pulse colors: orange for needs input, teal for done)

3. **Click-to-focus within container.** Clicking a pane entry in the widget list focuses that specific pane in the WT container (using WT move-focus or coordinate-based click).

4. **Auto-tile toggle.** When enabled (default for agent teams), Conductor doesn't tile the container -- it lets WT handle internal pane layout. The existing tiling controls apply to other independent windows only.

#### Modified Behavior

- `window_manager.py` already discovers Windows Terminal windows. It now also distinguishes between:
  - **Managed container windows** (launched by Conductor for agent teams)
  - **Independent windows** (user-opened, existing behavior)
- `terminal_reader.py` already reads terminal text via UI Automation. It now reads **per-pane** within a container by enumerating TermControl children of the container's accessibility tree.

---

## Implementation Phases

### Phase 1: tmux Shim + Named Pipe Server (Foundation)

**Goal:** Claude Code can call `tmux.exe` and Conductor receives the messages.

**Files to create:**
- `shim/tmux.py` — Parse tmux CLI arguments, send JSON over named pipe, print response to stdout
- `shim/build_shim.py` — PyInstaller build script for `tmux.exe`
- `widget/shim_server.py` — Named pipe listener thread, request dispatch

**Files to modify:**
- `widget/app.py` — Start the shim server thread on launch
- `widget/config.py` — Add named pipe path, session config constants

**Validation:** Run `tmux -V` and confirm it returns the fake version. Run `tmux has-session -t test` and confirm Conductor logs the request.

### Phase 2: Pane Spawning + Session Lifecycle

**Goal:** `tmux split-window` creates real WT panes. `tmux kill-pane` closes them.

**Files to create:**
- `widget/session_manager.py` — TeamSession and PaneInfo dataclasses, session lifecycle methods
- `widget/wt_integration.py` — `launch_container()`, `split_pane()`, pane focus management

**Files to modify:**
- `widget/shim_server.py` — Wire `split-window` and `kill-pane` commands to session manager
- `widget/window_manager.py` — Add container window tracking, distinguish managed vs independent windows

**Validation:** Start Conductor. From a terminal (simulating Claude Code), run:
```
tmux split-window -h -c C:\Users\bruce\project
tmux split-window -v -c C:\Users\bruce\project
tmux list-panes -F "#{pane_id}"
```
Confirm two new panes appear in the WT container.

### Phase 3: send-keys + Full Agent Teams Flow

**Goal:** Complete end-to-end agent teams. Claude Code spawns teammates that actually start working.

**Files to modify:**
- `widget/wt_integration.py` — Implement `send_keys_to_pane()` (Option A: focus-and-type)
- `widget/shim_server.py` — Wire `send-keys` and `display-message` commands
- `widget/session_manager.py` — Parse agent ID/name from `send-keys` text

**Environment setup:** The lead session must have these env vars:
```
TMUX=/tmp/conductor-shim,0,0
TMUX_PANE=%0
CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
```

**Validation:** Launch a lead Claude Code session in the container. Tell it to create an agent team. Confirm teammates spawn as WT panes and begin working.

### Phase 4: Enhanced Widget UI

**Goal:** Conductor widget shows agent team state with per-pane attention monitoring.

**Files to modify:**
- `widget/ui.py` — Add session grouping in window list, per-pane role/name display
- `widget/terminal_reader.py` — Per-pane text reading within a container (enumerate TermControl children)
- `widget/app.py` — Connect session manager events to UI updates

**Validation:** With an agent team running, confirm the widget shows each teammate with correct attention state. Orange pulse on teammates that need input. Teal on those that are done.

### Phase 5: Polish + Edge Cases

**Goal:** Handle real-world usage patterns.

- **Pane exit handling:** Detect when a teammate's process exits. Remove from session manager, update widget. WT handles visual rebalancing.
- **Session cleanup:** When the lead agent exits or the user closes the container, clean up all session state.
- **Multiple teams:** Support multiple container windows (different projects) simultaneously.
- **Error handling:** Shim timeouts (Conductor not running), WT launch failures, pane focus failures.
- **Settings:** Configurable container layout (lead-left vs lead-top), default working directory, auto-start on launch.

---

## File Structure (Final)

```
claude-conductor/
├── main.py                      # Entry point (existing, no changes)
├── widget/
│   ├── app.py                   # MODIFIED: Start shim server, wire session events
│   ├── ui.py                    # MODIFIED: Agent team UI elements, session grouping
│   ├── window_manager.py        # MODIFIED: Container vs independent window tracking
│   ├── monitor_manager.py       # No changes
│   ├── terminal_reader.py       # MODIFIED: Per-pane reading within container
│   ├── tiling.py                # No changes (used for independent windows only)
│   ├── config.py                # MODIFIED: New constants for pipe, session config
│   ├── utils.py                 # No changes
│   ├── shim_server.py           # NEW: Named pipe server for tmux shim
│   ├── session_manager.py       # NEW: Agent team session lifecycle
│   └── wt_integration.py        # NEW: Windows Terminal pane control
├── shim/
│   ├── tmux.py                  # NEW: tmux shim script
│   ├── pane_relay.py            # NEW: Per-pane named pipe relay for send-keys
│   ├── build_shim.py            # NEW: PyInstaller build script
│   └── README.md                # NEW: Shim installation guide
├── requirements.txt             # MODIFIED: Add any new deps (likely none)
└── .gitignore
```

---

## Risk Assessment

| Risk | Impact | Status | Mitigation |
|---|---|---|---|
| `wt.exe split-pane` doesn't work from external process | Blocks Phase 2 | ✅ MITIGATED | Spike tested — works with `-w <name>` targeting |
| `send-keys` unreliable | Degrades Phase 3 | ✅ MITIGATED | Using per-pane named pipe relay (Option C) from the start |
| tmux.exe PATH conflicts with WSL/Git Bash tmux | Blocks Phase 1 | ✅ MITIGATED | Conductor prepends shim dir to PATH for managed sessions only |
| UI Automation can't enumerate individual panes in WT | Blocks Phase 4 | ⚠️ OPEN | Test with existing `terminal_reader.py` early in Phase 2. Fallback: monitor by process PID instead of TermControl HWND |
| Claude Code changes tmux command patterns | Breaks shim | ⚠️ OPEN | Pin to known Claude Code version (v2.1.89). Monitor `TmuxBackend` source for changes. Shim logs unrecognized commands for debugging |
| Windows Terminal doesn't equalize pane sizes | Visual issue | ⚠️ LOW | WT auto-equalizes within a split direction. Can use `-s` flag for explicit sizing if needed |
| Claude Code adds new tmux commands we don't handle | Degrades functionality | ⚠️ OPEN | Shim returns error for unknown commands and logs them. Review after each Claude Code update |
| PyInstaller `tmux.exe` startup latency | Degrades UX | ⚠️ LOW | Claude Code calls tmux synchronously. PyInstaller single-file executables can take 1-2s on first launch (unpacking). Consider `--onedir` if cold-start is too slow |

---

## Testing Strategy

### Unit Tests
- Shim argument parsing for all 8 tmux commands
- Session manager CRUD operations
- Format string (`#{pane_id}`, `#{pane_pid}`) rendering

### Integration Tests
- Shim → Named Pipe → Conductor round-trip
- `split-window` → WT pane appears → UI Automation finds TermControl
- `send-keys` → keystrokes arrive in target pane
- `kill-pane` → pane closes → session manager updates

### End-to-End Test
1. Start Conductor
2. In a terminal with TMUX env vars set, run Claude Code
3. Tell Claude to create an agent team with 3 teammates
4. Confirm container window auto-launches on first split-window
5. Confirm teammates spawn as panes in the container
6. Confirm Conductor widget shows all panes with attention states
7. Interact with a teammate that needs input
8. Confirm panes close when teammates finish

---

## Dependencies

**No new Python packages required.** Everything builds on existing dependencies:
- `pywin32` — Named pipes (`win32pipe`, `win32file`), window management, `SendInput`
- `comtypes` — UI Automation (existing terminal reader)
- `subprocess` — `wt.exe` invocation
- `json` — Pipe protocol
- `threading` — Pipe server thread

**Build dependency:**
- `PyInstaller` — Compile shim to `tmux.exe` (dev-time only)

---

## Resolved Questions

1. **WT split-pane from external process:** ✅ RESOLVED. `wt.exe -w <name> split-pane` targets a named window reliably. Spike tested successfully — splits land in the correct window without creating new tabs. Uses `-w` flag with a Conductor-assigned window name (e.g., `claude-team-{session}`).

2. **send-keys targeting:** ✅ RESOLVED. Using per-pane named pipe helpers (`pane_relay.py`). Each pane runs a relay script that reads from `\\.\pipe\claude-conductor-pane-{pane_id}` and forwards to child stdin. No focus switching needed.

3. **Pane ID stability:** ✅ RESOLVED. WT may rebuild its accessibility tree on pane close. Mitigation: cache TermControl HWNDs and invalidate on pane lifecycle events (create/kill). Re-enumerate only when pane count changes.

4. **Container launch trigger:** ✅ RESOLVED. Lazy start — auto-launch the container on the first `split-window` command. No explicit button required.

5. **tmux.exe PATH priority:** ✅ RESOLVED. Claude Code hardcodes `"tmux"` at cli.js line 2176 (`dZ="tmux"`). No config to override the binary name. Solution: Conductor prepends the shim directory to PATH when launching the lead session's terminal. This scopes the override to Conductor-managed sessions only — no system-wide PATH pollution.

6. **in-process mode viability:** ✅ RESOLVED. `teammateMode: "in-process"` runs teammates as async tasks within the same Node.js process — no new windows, no new processes, nothing visible. On Windows, `"auto"` mode always falls back to in-process because tmux/iTerm2 are unavailable. **The tmux shim is the only path to visible agent team panes on Windows.**

7. **Shim prerequisites for tmux mode on Windows:** ✅ RESOLVED. Two conditions must be met for Claude Code to use tmux mode instead of in-process:
   - `tmux -V` must succeed (shim returns fake version string)
   - `TMUX` env var must be set in the lead session (so `process.env.TMUX` check returns true)
   - Conductor sets both when launching the lead pane

8. **tmux `-L` socket flag:** ✅ RESOLVED. Claude Code calls `tmux -L claude-swarm-<pid> <cmd>` for session isolation. The shim must accept and ignore the `-L` flag (since we use named pipes, not Unix sockets).

## Open Questions

1. **WT `-V`/`-H` flag semantics:** The doc currently maps tmux `-h` → WT `-V` and tmux `-v` → WT `-H`. This needs empirical verification during Phase 2 since Microsoft's documentation has historically been inconsistent about which flag means "split line direction" vs "pane arrangement direction."
