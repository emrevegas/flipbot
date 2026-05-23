# Custom Card Assets

Place your own card PNG images here to replace the auto-generated ones.

## Naming Convention

`{rank}{suit}.png`

| Rank | Suit | Example |
|------|------|---------|
| A, 2–9, 10, J, Q, K | h (♥ Hearts) | `Ah.png`, `10h.png`, `Kh.png` |
| | d (♦ Diamonds) | `Ad.png`, `2d.png` |
| | c (♣ Clubs) | `Ac.png`, `Jc.png` |
| | s (♠ Spades) | `As.png`, `Qs.png` |

## Card Back

Name the back image: `back.png`

## Recommended Size

**71 × 100 px** (standard poker card ratio). The bot scales them automatically.

## Notes

- If any image is missing, the bot auto-generates it with PIL on startup.
- You can replace individual cards — missing ones still auto-generate.
- PNG format only.
