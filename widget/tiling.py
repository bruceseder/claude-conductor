import math
from .config import TILE_GAP, CASCADE_OFFSET


def tile_grid(windows, area, gap=TILE_GAP):
    """Arrange windows in an optimal grid."""
    n = len(windows)
    if n == 0:
        return []

    left, top, right, bottom = area
    w_total = right - left
    h_total = bottom - top

    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)

    cell_w = (w_total - gap * (cols + 1)) / cols
    cell_h = (h_total - gap * (rows + 1)) / rows

    result = []
    for i, win in enumerate(windows):
        row = i // cols
        col = i % cols
        x = left + gap + col * (cell_w + gap)
        y = top + gap + row * (cell_h + gap)
        result.append((win.hwnd, x, y, cell_w, cell_h))

    return result


def tile_horizontal(windows, area, gap=TILE_GAP):
    """Side by side (vertical splits). Falls back to grid if > 4."""
    n = len(windows)
    if n == 0:
        return []
    if n > 4:
        return tile_grid(windows, area, gap)

    left, top, right, bottom = area
    w_total = right - left
    h_total = bottom - top

    cell_w = (w_total - gap * (n + 1)) / n
    cell_h = h_total - gap * 2

    result = []
    for i, win in enumerate(windows):
        x = left + gap + i * (cell_w + gap)
        y = top + gap
        result.append((win.hwnd, x, y, cell_w, cell_h))

    return result


def tile_vertical(windows, area, gap=TILE_GAP):
    """Stacked top to bottom (horizontal splits). Falls back to grid if > 4."""
    n = len(windows)
    if n == 0:
        return []
    if n > 4:
        return tile_grid(windows, area, gap)

    left, top, right, bottom = area
    w_total = right - left
    h_total = bottom - top

    cell_w = w_total - gap * 2
    cell_h = (h_total - gap * (n + 1)) / n

    result = []
    for i, win in enumerate(windows):
        x = left + gap
        y = top + gap + i * (cell_h + gap)
        result.append((win.hwnd, x, y, cell_w, cell_h))

    return result


def tile_cascade(windows, area, offset=CASCADE_OFFSET):
    """Cascade windows with offset."""
    n = len(windows)
    if n == 0:
        return []

    left, top, right, bottom = area
    w_total = right - left
    h_total = bottom - top

    win_w = int(w_total * 0.75)
    win_h = int(h_total * 0.75)

    max_offset_x = w_total - win_w
    max_offset_y = h_total - win_h

    result = []
    for i, win in enumerate(windows):
        dx = (i * offset) % max(max_offset_x, 1)
        dy = (i * offset) % max(max_offset_y, 1)
        result.append((win.hwnd, left + dx, top + dy, win_w, win_h))

    return result


def distribute_across_monitors(windows, monitor_areas, mode='grid', gap=TILE_GAP):
    """Spread windows evenly across monitors, then tile each group."""
    n = len(windows)
    m = len(monitor_areas)
    if n == 0 or m == 0:
        return []

    # Round-robin assign windows to monitors
    groups = [[] for _ in range(m)]
    for i, win in enumerate(windows):
        groups[i % m].append(win)

    tile_fn = {
        'grid': tile_grid,
        'horizontal': tile_horizontal,
        'vertical': tile_vertical,
        'cascade': tile_cascade,
    }.get(mode, tile_grid)

    result = []
    for group, area in zip(groups, monitor_areas):
        if group:
            result.extend(tile_fn(group, area, gap))

    return result


def calculate_layout(mode, windows, area, gap=TILE_GAP):
    """Main entry point for layout calculation."""
    if mode == 'grid':
        return tile_grid(windows, area, gap)
    elif mode == 'horizontal':
        return tile_horizontal(windows, area, gap)
    elif mode == 'vertical':
        return tile_vertical(windows, area, gap)
    elif mode == 'cascade':
        return tile_cascade(windows, area, gap)
    return tile_grid(windows, area, gap)
