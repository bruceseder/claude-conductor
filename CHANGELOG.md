# Changelog

All notable changes to Claude Conductor are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
