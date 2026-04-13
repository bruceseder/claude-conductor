# Window detection
WINDOW_CLASSES = ["CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass"]
TITLE_KEYWORDS = ["claude", "powershell", "pwsh", "bash", "cmd"]
TITLE_EXCLUDE = ["power-widget", "Power Widget", "Claude Conductor"]
# Browser window classes to ignore (Edge, Chrome, Firefox, etc.)
CLASS_EXCLUDE = ["Chrome_WidgetWin_1", "MozillaWindowClass", "ApplicationFrameWindow", "CabinetWClass"]

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
ATTENTION_COLOR_BRIGHT = "#ffcc55"
ATTENTION_COLOR_DIM = "#5a2e00"

# Idle/done pulse (teal-green) - Claude finished, waiting for next instruction
IDLE_COLOR = "#00cc88"
IDLE_COLOR_BRIGHT = "#66ffcc"
IDLE_COLOR_DIM = "#004d33"

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

# Claude network status
STATUS_POLL_INTERVAL_MS = 60000  # 60 seconds
STATUS_URL = "https://status.claude.com/api/v2/components.json"
STATUS_COMPONENTS = {
    "yyzkbfz2thpt": "Code",   # Claude Code
    "k8w3r06qmzrp": "API",    # Claude API (api.anthropic.com)
    "rwppv331jlwc":  "Web",   # claude.ai
}
STATUS_COLORS = {
    "operational": "#a6e3a1",           # green
    "degraded_performance": "#f9e2af",  # yellow
    "partial_outage": "#fab387",        # peach/orange
    "major_outage": "#f38ba8",          # red
    "unknown": "#6c7086",              # dim - can't reach
}

# Tiling
TILE_GAP = 6
CASCADE_OFFSET = 32

# Attention pulse animation
PULSE_INTERVAL_MS = 50  # ~20fps widget row animation
PULSE_SPEED = 0.045  # Radians per frame (full cycle ~3 seconds, gentler)
