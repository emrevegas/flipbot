import os
from dotenv import load_dotenv

load_dotenv()

TOKEN        = os.getenv("TOKEN", "")
PREFIX       = os.getenv("PREFIX", ".")
OWNER_IDS    = [int(x) for x in os.getenv("OWNER_ID", "0").split(",") if x.strip()]

POINTS_PER_USD       = float(os.getenv("POINTS_PER_USD", "100"))
RAKEBACK_RATE        = float(os.getenv("RAKEBACK_RATE", "0.05"))       # 5% of wagered
RAKEBACK_MIN_CLAIM   = float(os.getenv("RAKEBACK_MIN_CLAIM", "10"))
RAKEBACK_TIERS = [
    {"name": "Bronze",   "min_wagered": 0,       "rate": 0.03},
    {"name": "Silver",   "min_wagered": 5_000,   "rate": 0.05},
    {"name": "Gold",     "min_wagered": 25_000,  "rate": 0.08},
    {"name": "Platinum", "min_wagered": 100_000, "rate": 0.12},
    {"name": "Diamond",  "min_wagered": 500_000, "rate": 0.18},
]

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
