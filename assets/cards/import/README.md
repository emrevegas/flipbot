# Kart import

**VDS:** `assets/cards/*.png` repoda (git pull yeterli). `import/` klasörü git’e girmez.

Yeni kart seti için PNG’leri buraya koy, isimlendir, sonra (geliştirme makinesinde):

```bash
python scripts/import_cards.py
git add assets/cards/*.png assets/cards/display.json
git commit -m "Update card assets"
git push
```

## İsimlendirme (emoji ile aynı)

| Örnek | Anlam |
|-------|--------|
| `AC.png` | As ♣ |
| `0H.png` veya `10H.png` | 10 ♥ |
| `1D.png` | As ♦ (1 = As) |
| `KS.png` | Papaz ♠ |
| `CB.png` | Arka yüz |

- **Rank:** `A` `2`–`9` `0` veya `10` `J` `Q` `K` (veya As için `1`)
- **Suit:** `C` `H` `D` `S`

Script dosyaları `../Ah.png`, `../10h.png`, `../back.png` olarak kaydeder ve Pillow ile oyun boyutuna ölçekler.

## Boyut (GIF’te görünen)

`assets/cards/display.json` — varsayılan **108×150**. Değiştirmek için:

```bash
python scripts/import_cards.py --width 100 --height 140
```

## Hazır etiket haritası (VegasBet deste)

```bash
python scripts/import_cards.py --from-map
python scripts/import_cards.py
```

`rename_map.json` Cursor’daki dosya adlarını `AC.png` vb. ile eşler.
