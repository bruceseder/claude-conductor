# Window detection
WINDOW_CLASSES = ["CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass"]
TITLE_KEYWORDS = ["claude", "powershell", "pwsh", "bash", "cmd"]
TITLE_EXCLUDE = ["power-widget", "Power Widget"]

# UI Theme (dark, terminal aesthetic)
BG_COLOR = "#1e1e2e"
BG_SECONDARY = "#181825"
FG_COLOR = "#cdd6f4"
FG_DIM = "#6c7086"
ACCENT_COLOR = "#89b4fa"
CLAUDE_COLOR = "#f5c2e7"
HOVER_COLOR = "#313244"
BUTTON_BG = "#45475a"
BUTTON_HOVER = "#585b70"
BORDER_COLOR = "#585b70"
# Choice/decision pulse (orange) - Claude asking yes/no, 1/2/3
ATTENTION_COLOR = "#ff7b00"
ATTENTION_COLOR_BRIGHT = "#ffaa44"
ATTENTION_COLOR_DIM = "#3d1e00"

# Idle/done pulse (teal-green) - Claude finished, waiting for next instruction
IDLE_COLOR = "#00cc88"
IDLE_COLOR_BRIGHT = "#44ffbb"
IDLE_COLOR_DIM = "#002a1a"

FONT_FAMILY = "Cascadia Code"
FONT_FALLBACK = "Consolas"
FONT_SIZE = 9

# Widget dimensions
WIDGET_WIDTH = 340
WIDGET_MIN_HEIGHT = 200
WIDGET_MAX_HEIGHT = 700
ROW_HEIGHT = 32

# Refresh interval (ms)
REFRESH_INTERVAL_MS = 2000

# Tiling
TILE_GAP = 6
CASCADE_OFFSET = 32

# Attention pulse animation
PULSE_INTERVAL_MS = 50  # ~20fps widget row animation
PULSE_SPEED = 0.045  # Radians per frame (full cycle ~3 seconds, gentler)
