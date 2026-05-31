# Flipbot licensing (Ada bot)

No HTTP license API. The **Ada bot** (`ada_bot.py` + `ada.env`) manages licenses, balance shop, VDS SSH deploy, and GitHub releases.

## Owner setup

1. Copy `ada.env.example` → `ada.env`
2. Generate signing keys: `python licensing/control/generate_keys.py`
3. Set `ADA_BOT_TOKEN`, `OWNER_ID`, `LICENSE_PRIVATE_KEY_PATH`, GitHub secrets
4. Run: `python ada_bot.py`
5. Add GitHub repo secrets for Actions: `LICENSE_PRIVATE_KEY`, `LICENSE_PUBLIC_KEY`, `GITHUB_TOKEN`, `RELEASES_GITHUB_REPO`
6. `/build version:1.0.0` — triggers `.github/workflows/build-release.yml`
7. `/vds_manage register_release` — if DB was not auto-registered on build machine

## Customer flow (English, all in Discord)

1. Owner: `/vds_manage grant_balance` (or customer `/manage_bot buy_license`)
2. `/use_license` — activate key
3. `/manage_bot add_vds` — `host:port:user:password` + OS (`ubuntu` / `windows`)
4. `/manage_bot setup` — token, mnemonics, guild ID (Ubuntu VDS for compiled bot)
5. `/manage_bot start` | `stop` | `restart`

Compiled bot on VDS validates `license.dat` (Ed25519) and checks GitHub for updates. Ada bot syncs `license.dat` over SSH every 5 minutes.

## VDS rules

- One VDS per user while license is active
- Remove VDS → run `/use_license` again to unlock a new slot
- Windows: SSH test supported; **bot deploy requires Ubuntu** (Linux `.so` build)
