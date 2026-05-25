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

## Import folder (önerilen)

VegasBet kartlarını `import/` içine **AC.png**, **0H.png**, **CB.png** isimleriyle koy, sonra:

```bash
python scripts/import_cards.py
```

Detay: `import/README.md`

## Display size (GIF)

`display.json` — varsayılan **92×128**. Oyun sırasında Pillow (LANCZOS) ile ölçeklenir.

## Notes

- Küçük placeholder PNG'ler (<8 KB) eksik sayılır; büyük özel kartların üzerine yazılmaz.
- Eksik kartlar PIL ile üretilir.
- PNG format only.
