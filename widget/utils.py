import ctypes
import ctypes.wintypes
import json
import os
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone
import win32gui
import win32con
import pywintypes

from . import config as cfg


def setup_dpi_awareness():
    """Must be called BEFORE any tkinter import."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def force_set_foreground(hwnd):
    """Bring a window to the foreground, working around Windows restrictions."""
    try:
        if not win32gui.IsWindow(hwnd):
            return False

        foreground = win32gui.GetForegroundWindow()
        if foreground == hwnd:
            return True

        fg_thread = ctypes.windll.user32.GetWindowThreadProcessId(
            foreground, ctypes.byref(ctypes.wintypes.DWORD())
        )
        our_thread = ctypes.windll.kernel32.GetCurrentThreadId()

        attached = False
        if fg_thread != our_thread:
            attached = ctypes.windll.user32.AttachThreadInput(fg_thread, our_thread, True)

        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

            win32gui.SetForegroundWindow(hwnd)
            win32gui.BringWindowToTop(hwnd)
        except pywintypes.error:
            # Fallback: flash the window
            try:
                win32gui.FlashWindow(hwnd, True)
            except pywintypes.error:
                pass
        finally:
            if attached:
                ctypes.windll.user32.AttachThreadInput(fg_thread, our_thread, False)

        return True
    except (pywintypes.error, OSError):
        return False


def is_braille_char(c):
    """Check if character is in Unicode Braille range (Claude Code spinner)."""
    return 0x2800 <= ord(c) <= 0x28FF


def clean_title(title):
    """Strip Braille spinner chars and leading whitespace from window title."""
    cleaned = []
    leading = True
    for c in title:
        if leading and (is_braille_char(c) or c in (' ', '\u2733', '✳')):
            continue
        leading = False
        cleaned.append(c)
    return ''.join(cleaned).strip()


def is_claude_window(title):
    """Detect if a window title indicates a Claude Code session."""
    lower = title.lower()
    if 'claude' in lower:
        return True
    # Active spinner (Braille) = Claude working
    if has_spinner(title):
        return True
    # Static sparkle (✳) = Claude idle/waiting
    if '\u2733' in title or '✳' in title:
        return True
    return False


def has_spinner(title):
    """Check if a title has an active animated spinner (Braille chars only).

    U+2733 (✳) is a static Claude indicator (idle/waiting for input).
    Braille chars (U+2800-U+28FF) are the animated spinner (actively working).
    """
    for c in title:
        if is_braille_char(c):
            return True
    return False


def _parse_hex(hex_color):
    """Parse '#RRGGBB' into (r, g, b) ints."""
    return int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)


def lerp_color(hex1, hex2, t):
    """Linearly interpolate between two hex colors. t=0 gives hex1, t=1 gives hex2."""
    r1, g1, b1 = _parse_hex(hex1)
    r2, g2, b2 = _parse_hex(hex2)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f'#{r:02x}{g:02x}{b:02x}'


# --- DWM Window Border Color (Windows 11) ---
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_COLOR_DEFAULT = 0xFFFFFFFF


def _hex_to_colorref(hex_color):
    """Convert '#RRGGBB' to COLORREF (0x00BBGGRR)."""
    r, g, b = _parse_hex(hex_color)
    return (b << 16) | (g << 8) | r


def set_window_border_color(hwnd, hex_color, caption_color=None):
    """Set the DWM border and optionally title bar color. Windows 11 only."""
    try:
        colorref = ctypes.c_int(_hex_to_colorref(hex_color))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_BORDER_COLOR,
            ctypes.byref(colorref), ctypes.sizeof(colorref)
        )
        if caption_color:
            cap_ref = ctypes.c_int(_hex_to_colorref(caption_color))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_CAPTION_COLOR,
                ctypes.byref(cap_ref), ctypes.sizeof(cap_ref)
            )
    except Exception:
        pass


# --- Console Title (Windows Terminal) ---

TH32CS_SNAPPROCESS = 0x00000002


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.wintypes.DWORD),
        ("cntUsage", ctypes.wintypes.DWORD),
        ("th32ProcessID", ctypes.wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", ctypes.wintypes.DWORD),
        ("cntThreads", ctypes.wintypes.DWORD),
        ("th32ParentProcessID", ctypes.wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def _get_descendant_pids(parent_pid):
    """Get all descendant process PIDs using a single snapshot."""
    snapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == -1:
        return []
    try:
        children_map = {}
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        if ctypes.windll.kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                ppid = entry.th32ParentProcessID
                children_map.setdefault(ppid, []).append(entry.th32ProcessID)
                if not ctypes.windll.kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        ctypes.windll.kernel32.CloseHandle(snapshot)

    # BFS to collect all descendants
    result = []
    queue = list(children_map.get(parent_pid, []))
    visited = {parent_pid}
    while queue:
        pid = queue.pop(0)
        if pid in visited:
            continue
        visited.add(pid)
        result.append(pid)
        queue.extend(children_map.get(pid, []))
    return result


def set_window_title(hwnd, title):
    """Set the console title for a terminal window via AttachConsole + SetConsoleTitleW."""
    try:
        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        target_pid = pid.value
        if not target_pid:
            return

        # Try the window's own PID first, then all descendant processes
        pids_to_try = [target_pid] + _get_descendant_pids(target_pid)

        ctypes.windll.kernel32.FreeConsole()
        for p in pids_to_try:
            if ctypes.windll.kernel32.AttachConsole(p):
                ctypes.windll.kernel32.SetConsoleTitleW(title)
                ctypes.windll.kernel32.FreeConsole()
                return
    except Exception:
        pass


def reset_window_border_color(hwnd):
    """Reset window border and title bar to system default."""
    try:
        default = ctypes.c_int(DWMWA_COLOR_DEFAULT)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_BORDER_COLOR,
            ctypes.byref(default), ctypes.sizeof(default)
        )
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_CAPTION_COLOR,
            ctypes.byref(default), ctypes.sizeof(default)
        )
    except Exception:
        pass


# --- Claude Network Status ---

_STATUS_LABELS = {
    "operational": "OK",
    "degraded_performance": "Deg",
    "partial_outage": "Part",
    "major_outage": "Out",
    "unknown": "?",
}


def fetch_claude_status(callback):
    """Fetch Claude component statuses in a background thread.

    Calls callback(results) where results is a list of (short_name, status, label)
    tuples ordered by STATUS_COMPONENTS (Code, API, Web).
    """
    def _fetch():
        try:
            req = urllib.request.Request(cfg.STATUS_URL, headers={"User-Agent": "PowerWidget/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                by_id = {c["id"]: c.get("status", "unknown") for c in data.get("components", [])}
                results = []
                for comp_id, short_name in cfg.STATUS_COMPONENTS.items():
                    status = by_id.get(comp_id, "unknown")
                    results.append((short_name, status, _STATUS_LABELS.get(status, "?")))
                callback(results)
        except Exception:
            callback([(name, "unknown", "?") for name in cfg.STATUS_COMPONENTS.values()])

    t = threading.Thread(target=_fetch, daemon=True)
    t.start()


# --- Claude Usage Stats (the /usage session / week / Fable numbers) ---


def _usage_token():
    """Read the current OAuth access token from Claude Code's credentials file.

    Read fresh on every poll so token refreshes performed by Claude Code are
    picked up automatically. Returns None if the file or token is unavailable.
    """
    try:
        with open(os.path.expanduser(cfg.CREDENTIALS_PATH), "r", encoding="utf-8") as f:
            return json.load(f).get("claudeAiOauth", {}).get("accessToken")
    except Exception:
        return None


def _pace_percent(kind, resets_at):
    """Where usage 'should' be if the window is consumed evenly: the elapsed
    fraction of the window as a 0-100 percent. resets_at is the window end."""
    duration = cfg.USAGE_WINDOW_SECONDS.get(kind)
    if not duration or not resets_at:
        return None
    try:
        remaining = (datetime.fromisoformat(resets_at) - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, min(100.0, (duration - remaining) / duration * 100.0))
    except Exception:
        return None


def _parse_usage(data):
    """Build the (key, label, percent, pace, text) metric list from a usage
    response. `percent` drives the gauge fill/color; `text` overrides the
    readout label (None => show "<percent>%"); `pace` places the marker (None
    => no marker).

    Session and weekly-all come from the always-present top-level `five_hour` /
    `seven_day` fields — the `limits` array can omit inactive entries, which
    would otherwise blank those two gauges. The scoped weekly (Fable) lives only
    in the `limits` array, so it's read from there (label from the model name).
    (Extra-usage credits are surfaced separately via _spend_info, not here.)
    """
    def _from_top(field, kind):
        src = data.get(field) or {}
        return src.get("utilization"), _pace_percent(kind, src.get("resets_at"))

    sess_pct, sess_pace = _from_top("five_hour", "session")
    week_pct, week_pace = _from_top("seven_day", "weekly_all")

    scoped_pct = scoped_pace = None
    scoped_label = "Fable"
    for lim in data.get("limits", []):
        if lim.get("kind") == "weekly_scoped":
            scoped_pct = lim.get("percent")
            scoped_pace = _pace_percent("weekly_scoped", lim.get("resets_at"))
            model = ((lim.get("scope") or {}).get("model") or {}).get("display_name")
            if model:
                scoped_label = model
            break

    return [
        ("session", "Sess", sess_pct, sess_pace, None),
        ("weekly_all", "Week", week_pct, week_pace, None),
        ("weekly_scoped", scoped_label, scoped_pct, scoped_pace, None),
    ]


def _spend_info(spend):
    """Extra-usage readout for the status-bar gauge: (used_percent, '$165 / $200'),
    or None when extra usage is disabled/absent. Text is None if amounts missing."""
    if not spend or not spend.get("enabled"):
        return None
    used = spend.get("used") or {}
    limit = spend.get("limit") or {}
    exp = used.get("exponent", limit.get("exponent", 2))
    um, lm = used.get("amount_minor"), limit.get("amount_minor")
    text = None
    if um is not None and lm is not None:
        sym = "$" if used.get("currency") == "USD" else ""
        text = f"{sym}{int(round(um / 10 ** exp))} / {sym}{int(round(lm / 10 ** exp))}"
    return spend.get("percent"), text


def fetch_claude_usage(callback):
    """Fetch Claude usage percentages in a background thread.

    Calls callback(metrics, status, spend_text):
      metrics — list of (key, label, percent, pace, text) tuples, else None
      status  — "ok" | "rate_limited" | "error"
      spend   — (used_percent, "$165 / $200") for the status-bar Extra gauge, or None

    metrics is None (not a blank list) on failure so the caller can keep the
    last-good gauges instead of wiping them. The /usage endpoint is aggressively
    rate-limited, so 429 is surfaced distinctly to drive backoff.
    """
    def _fetch():
        token = _usage_token()
        if not token:
            callback(None, "error", None)
            return
        try:
            req = urllib.request.Request(cfg.USAGE_URL, headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": cfg.USAGE_OAUTH_BETA,
                "anthropic-version": "2023-06-01",
                "User-Agent": "PowerWidget/1.0",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            callback(None, "rate_limited" if e.code == 429 else "error", None)
            return
        except Exception:
            callback(None, "error", None)
            return

        callback(_parse_usage(data), "ok", _spend_info(data.get("spend")))

    t = threading.Thread(target=_fetch, daemon=True)
    t.start()
