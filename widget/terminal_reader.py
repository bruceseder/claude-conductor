"""Read terminal text from Windows Terminal windows via UI Automation."""

import comtypes
import comtypes.client

# Initialize UI Automation COM
comtypes.client.GetModule('UIAutomationCore.dll')
from comtypes.gen.UIAutomationClient import (
    CUIAutomation, IUIAutomation, IUIAutomationTextPattern,
    UIA_TextPatternId,
)

_uia = None


def _get_uia():
    global _uia
    if _uia is None:
        _uia = comtypes.CoCreateInstance(
            CUIAutomation._reg_clsid_,
            interface=IUIAutomation,
            clsctx=comtypes.CLSCTX_INPROC_SERVER,
        )
    return _uia


def get_terminal_lines(hwnd, last_n=15):
    """Extract the last N lines of visible text from a Windows Terminal window.

    Returns a list of strings, or None if text can't be read.
    """
    try:
        uia = _get_uia()
        el = uia.ElementFromHandle(hwnd)
        walker = uia.ControlViewWalker

        # Recursively find the TermControl element
        tc = _find_term_control(walker, el, depth=0)
        if not tc:
            return None

        tp = tc.GetCurrentPattern(UIA_TextPatternId)
        if not tp:
            return None

        text_pattern = tp.QueryInterface(IUIAutomationTextPattern)
        doc = text_pattern.DocumentRange
        # Read the full buffer (-1 = unlimited, typically < 1MB, ~10ms)
        text = doc.GetText(-1)
        lines = text.strip().split('\n')
        return lines[-last_n:]
    except Exception:
        return None


def _find_term_control(walker, element, depth):
    """Walk the UIA tree to find the first TermControl element."""
    if depth > 6:
        return None
    child = walker.GetFirstChildElement(element)
    count = 0
    while child and count < 15:
        try:
            cname = child.CurrentClassName or ''
            if cname == 'TermControl':
                return child
            result = _find_term_control(walker, child, depth + 1)
            if result:
                return result
        except Exception:
            pass
        child = walker.GetNextSiblingElement(child)
        count += 1
    return None


# --- Attention State Detection ---

# The most reliable indicator of Claude Code's choice UI is
# "Esc to cancel" in the footer. We use that plus other patterns.
# All checked against stripped lowercase text.
CHOICE_PATTERNS = [
    'esc to cancel',             # Claude Code choice UI footer (MOST RELIABLE)
    '(y/n)',                     # Yes/no confirmation
    '(yes/no)',                  # Yes/no confirmation
    'do you want to proceed',   # Permission prompt
]


def detect_attention_type(hwnd):
    """Determine what kind of attention a terminal window needs."""
    lines = get_terminal_lines(hwnd, last_n=30)
    if not lines:
        return None
    return _detect_attention_from_lines(lines)


def _detect_attention_from_lines(lines):
    """Shared attention detection logic for both single-window and per-pane reads.

    Strategy:
    - Check last 10 lines for choice patterns (tight window, avoids stale matches)
    - Check last 20 lines for idle indicators (wider window, bullet and > may be
      above recent output like diffs or tables)
    - If neither found, no clear signal

    Returns:
        'choice'  - Claude is asking a question or needs approval
        'idle'    - Claude is done, waiting for next instruction
        None      - Could not determine
    """
    # Check last 10 lines for choice patterns
    choice_lines = [line.strip().lower() for line in lines[-10:] if line.strip()]
    choice_text = '\n'.join(choice_lines)

    for pattern in CHOICE_PATTERNS:
        if pattern in choice_text:
            return 'choice'

    # Check the LAST non-empty line specifically.
    last_nonempty = None
    for line in reversed(lines):
        s = line.strip()
        if s:
            last_nonempty = s
            break

    if last_nonempty:
        if last_nonempty == '\u25cf' or last_nonempty == '●':
            return 'idle'
        if last_nonempty == '>' or last_nonempty == '> ':
            return 'idle'

    # Wider window for idle
    idle_lines = [line.strip() for line in lines[-20:] if line.strip()]

    for s in idle_lines:
        if s == '\u25cf' or s == '●':
            return 'idle'
        if s == '>' or s == '> ':
            return 'idle'

    # Check if a bare > prompt exists AFTER the last bullet
    last_bullet_idx = -1
    for i in range(len(lines) - 1, -1, -1):
        s = lines[i].strip()
        if s.startswith('\u25cf') or s.startswith('●'):
            last_bullet_idx = i
            break

    if last_bullet_idx >= 0:
        for i in range(last_bullet_idx + 1, len(lines)):
            s = lines[i].strip()
            if s == '>' or s == '> ':
                return 'idle'

    return None
