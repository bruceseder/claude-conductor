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
HOVER_COLOR = "#313244"
BUTTON_BG = "#45475a"
BUTTON_HOVER = "#585b70"
BORDER_COLOR = "#585b70"
# Choice/decision pulse (HDR orange) - Claude asking yes/no, 1/2/3 (waiting)
ATTENTION_COLOR = "#ff7a00"
ATTENTION_COLOR_BRIGHT = "#ffd98a"
ATTENTION_COLOR_DIM = "#5a2e00"

# Idle/done pulse (HDR green) - Claude finished, waiting for next instruction
IDLE_COLOR = "#00e676"
IDLE_COLOR_BRIGHT = "#a6ffce"
IDLE_COLOR_DIM = "#004d2a"

# Working/processing (electric blue) - Claude is actively running a task
WORKING_COLOR = "#1e9bff"
WORKING_COLOR_BRIGHT = "#79d2ff"
WORKING_COLOR_DIM = "#002b52"

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

# Claude usage stats (the /usage session / week / Fable numbers).
# This is the SUBSCRIPTION usage surface (OAuth token), which Anthropic says to
# check SPARINGLY: frequent polling trips its rate limit (429) and can consume a
# small % of the 5-hour session limit. Session is a 5h window and weekly is 7d,
# so a slow poll loses nothing visually. Do NOT drop this below a few minutes.
USAGE_POLL_INTERVAL_MS = 900000  # every 15 minutes
USAGE_MAX_POLL_INTERVAL_MS = 1800000  # backoff cap (30 min) when rate-limited
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
USAGE_OAUTH_BETA = "oauth-2025-04-20"
CREDENTIALS_PATH = "~/.claude/.credentials.json"  # OAuth token read fresh each poll
# Metrics we surface, in display order: (limits[].kind, fallback label)
USAGE_METRICS = [
    ("session", "Sess"),        # current 5-hour session window
    ("weekly_all", "Week"),     # current week, all models
    ("weekly_scoped", "Fable"),  # current week, most-capable model (label from API)
    ("credits", "Extra"),       # extra-usage credit balance (gauge=used%, text=$ available)
]
# Window length per metric; used to place the pace marker (elapsed fraction of
# the window = where usage "should" be if consumed evenly). resets_at from the
# API is the window end.
USAGE_WINDOW_SECONDS = {
    "session": 5 * 3600,
    "weekly_all": 7 * 86400,
    "weekly_scoped": 7 * 86400,
}
USAGE_BAR_HEIGHT = 26  # height of the usage-stats row (px); reserved in auto-resize
USAGE_GAUGE_W = 18  # mini-bar gauge width (px) — narrow so 4 cells fit the 340px row
USAGE_GAUGE_H = 8   # mini-bar gauge height (px)
USAGE_TRACK_COLOR = "#313244"  # unfilled gauge track
USAGE_PACE_COLOR = "#ff3355"   # thin pace/target marker line
# Fill color by how full the metric is (green -> amber -> red)
USAGE_COLOR_LOW = "#a6e3a1"    # < 50%
USAGE_COLOR_MID = "#f9e2af"    # 50-79%
USAGE_COLOR_HIGH = "#fab387"   # 80-94%
USAGE_COLOR_CRIT = "#f38ba8"   # >= 95%

# Tiling
TILE_GAP = 6
CASCADE_OFFSET = 32

# Attention pulse animation
PULSE_INTERVAL_MS = 80  # ~12fps widget row animation
PULSE_SPEED = 0.095  # Radians per frame; scaled with PULSE_INTERVAL_MS to keep the same wall-clock cycle
