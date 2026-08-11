# Changelog

All notable changes to Claude Conductor are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed

- **The row pulse now rolls left to right.** Instead of the whole row brightening
  and dimming in place, the glow sweeps in from the left edge, lights the full
  row in the state color, then drains off the right. Rows are now drawn as a
  single `Canvas` painting `PULSE_SWEEP_STRIPS` (20) vertical bands rather than a
  `Frame` of `Label`s — a label background can only hold one flat color, so the
  roll wasn't expressible with the old structure. Each band samples the same sine
  wave with its phase lagged by distance from the left edge
  (`PULSE_SWEEP_LAG`, 2.4 rad end to end); set that to `0` for the previous
  uniform pulse. Measured at 9.5ms per frame with 12 pulsing rows, against the
  80ms frame budget. Window borders still pulse flat, since DWM takes one color
  per window.
- **Working (blue) rows now pulse in the widget list.** Previously only their
  window border pulsed and the row sat static; the row itself now carries the
  blue pulse and roll like the orange and green states. Consequently the hover
  highlight no longer applies to working rows — a pulsing row is already lit —
  and remains only on non-Claude rows.
- Row text is now fitted to the pixel width left by the time/index/indicator
  items rather than truncated at a fixed 38 characters, and inline rename floats
  an `Entry` over the row canvas in place of the hidden title item.

### Documentation

- **Minimized restore tab** now has its own README section covering the aggregate
  bolt states, the priority order, and the implementation details (dedicated
  12fps pulse loop, faked border under `overrideredirect`, alpha-staged reveal).
- **Agent Teams modules** (`session_manager.py`, `wt_integration.py`,
  `shim_server.py`, `shim/`, `launch_team.py`) are now listed in the project
  structure and described in an "Experimental" section that states plainly they
  are not imported by the running application.

## [0.9.0] - 2026-07-24

First tagged release. Everything below is the state of the widget as used daily.

### Fixed

- **Monitor-menu resource leak.** `_rebuild_monitor_menu` created a new `tk.Menu`
  on every rebuild and only re-pointed the Menubutton at it, orphaning the old
  menu — which stayed a child of the Menubutton and leaked one Windows USER
  object per rebuild (~every 30s). Over a long session this destabilized the
  widget. The menu is now built once and repopulated in place, and the rebuild
  is skipped entirely when the monitor list is unchanged.
- **Refresh-loop multiplication.** The refresh button and the tile / minimize-all
  / restore-all handlers each scheduled an independent, self-perpetuating
  `after()` refresh loop. Every such action added another permanent loop,
  multiplying polling load, UI Automation reads, and CPU — and inflating
  per-project tracked time (each cycle adds a flat increment regardless of
  elapsed wall-clock). All refresh triggers now route through a single owned
  callback (`_schedule_refresh`) that cancels any pending refresh before
  scheduling, so exactly one loop ever runs.
- **Duplicate pulse-animation chains.** The attention-pulse loop was gated only by
  a boolean flag, so when attention rows briefly drained and reappeared before a
  queued callback fired, a second animation chain could start — doubling the
  pulse rate. The pending callback id is now tracked and a fresh chain only
  starts when none is queued.

### Added

- **Exception logging.** The main refresh loop now logs swallowed exceptions to a
  rotating `power_widget.log` (capped at ~1 MB: 512 KB active + one backup).
  Because the widget runs under `pythonw` (no console), failures were previously
  invisible; the file is only created on the first error, so its absence still
  means a clean run. The log is gitignored.
- **Live usage-stats gauges.** A compact row of mini-gauges mirroring `/usage`:
  5-hour session, weekly (all models), weekly (most-capable model), and an extra-
  usage credits gauge showing `$used / $cap` on the status row. Each fills
  green → amber → red by utilization and carries a red pace marker at the
  wall-clock-linear position. Polled every 15 minutes against Anthropic's OAuth
  usage endpoint, with 429 backoff and last-good retention.
- **Minimized restore tab.** A right-edge tab with a pulsing bolt whose color
  reflects aggregate state across all sessions (orange = something needs a
  decision, blue = something is working, green = all idle), so the widget stays
  useful while collapsed.
- **Claude service status indicators.** Code / API / Web dots polled from
  `status.claude.com` every 60 seconds.

### Changed

- **Three-state color language.** Working is now an electric-blue pulse rather
  than no pulse at all, applied consistently across row colors, window borders,
  and the restore-tab bolt. Working detection moved from the window-title braille
  spinner to the terminal's `Esc to interrupt` footer, which does not drop out
  during tool calls.

[Unreleased]: https://github.com/bruceseder/claude-conductor/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/bruceseder/claude-conductor/releases/tag/v0.9.0
