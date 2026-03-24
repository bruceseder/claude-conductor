import ctypes
import ctypes.wintypes
import win32gui
import win32con
import pywintypes


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
DWMWA_COLOR_DEFAULT = 0xFFFFFFFF


def _hex_to_colorref(hex_color):
    """Convert '#RRGGBB' to COLORREF (0x00BBGGRR)."""
    r, g, b = _parse_hex(hex_color)
    return (b << 16) | (g << 8) | r


def set_window_border_color(hwnd, hex_color):
    """Set the DWM border color of a window. Windows 11 only."""
    try:
        colorref = ctypes.c_int(_hex_to_colorref(hex_color))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_BORDER_COLOR,
            ctypes.byref(colorref), ctypes.sizeof(colorref)
        )
    except Exception:
        pass


def reset_window_border_color(hwnd):
    """Reset window border to system default."""
    try:
        default = ctypes.c_int(DWMWA_COLOR_DEFAULT)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_BORDER_COLOR,
            ctypes.byref(default), ctypes.sizeof(default)
        )
    except Exception:
        pass
