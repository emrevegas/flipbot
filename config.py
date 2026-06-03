import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
LUCI_QUEUE_DIR = Path(os.getenv("LUCI_QUEUE_DIR", str(DATA_DIR / "luci")))

TOKEN        = os.getenv("TOKEN", "")
PREFIX       = os.getenv("PREFIX", ".")
BOT_DISPLAY_NAME = os.getenv("BOT_DISPLAY_NAME", "VegasBet")
OWNER_IDS    = [int(x) for x in os.getenv("OWNER_ID", "0").split(",") if x.strip()]
SUPER_ADMIN_ID = os.getenv("SUPER_ADMIN_ID", str(OWNER_IDS[0]) if OWNER_IDS else "0")

POINTS_PER_USD       = float(os.getenv("POINTS_PER_USD", "100"))
# Rakeback tiers are managed in the database via /panel rakeback — not in .env
RAKEBACK_MIN_CLAIM   = float(os.getenv("RAKEBACK_MIN_CLAIM", "10"))

# Affiliate: referrer earns 10% of (daily deposits − daily withdrawals) of each referred user
AFFILIATE_NET_RATE   = 0.10
AFFILIATE_MIN_CLAIM  = float(os.getenv("AFFILIATE_MIN_CLAIM", "10"))

# Card styling
CARD_BG_COLOR     = (13, 17, 30)        # dark navy
CARD_ACCENT_COLOR = (30, 215, 96)       # Spotify-ish green
CARD_TEXT_PRIMARY = (255, 255, 255)
CARD_TEXT_MUTED   = (140, 150, 170)
CARD_HIGHLIGHT    = (56, 189, 248)      # sky blue for big numbers
CARD_GOLD         = (255, 196, 0)
CARD_BORDER       = (30, 40, 60)
