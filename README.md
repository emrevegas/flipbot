# FlipBot

Prefix-command Discord bot with image-based responses.

## Features

- `.balance [@user]` — balance card (Pillow image)
- `.leaderboard` — top 10 balances card
- `.pay @user amount` — transfer points
- `.add @user amount [note]` — admin: add points
- `.remove @user amount [note]` — admin: remove points
- `.setbal @user amount` — admin: set exact balance
- `.resetbal @user` — admin: reset to 0
- `.promo create/delete/list/toggle` — admin: manage promos
- `.redeem CODE` — redeem promo code
- `.affiliate create/stats/use/claim/referred` — affiliate system
- `.rakeback` — rakeback status card
- `.rakeback claim` — claim accumulated rakeback
- `.rakeback tiers` — view tier table
- `.history @user` — admin: transaction history
- `/user_panel` — slash: full profile panel with card buttons
- `/panel stats/user/leaderboard` — slash: admin panel

## Setup

```bash
cp .env.example .env
# edit .env with your TOKEN, OWNER_ID, etc.
pip install -r requirements.txt
python bot.py
```

## Fonts (for image cards)

Place `regular.ttf` and `bold.ttf` in `assets/fonts/`.
Recommended: [Inter](https://fonts.google.com/specimen/Inter) — free & clean.
Without fonts the bot uses Pillow's built-in default font.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| TOKEN | — | Discord bot token |
| PREFIX | `.` | Command prefix |
| OWNER_ID | — | Your Discord user ID (comma-separated for multiple) |
| POINTS_PER_USD | 100 | Points per $1 USD |
| RAKEBACK_RATE | 0.05 | Default rakeback rate |
| AFFILIATE_FTD_RATE | 0.10 | First-deposit commission rate |
| AFFILIATE_EDGE_RATE | 0.25 | Lifetime house-edge commission rate |
