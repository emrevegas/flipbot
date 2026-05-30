"""Licensed bot entry — license_guard (.so) then flipbot_core (.so)."""

from __future__ import annotations

import asyncio
import sys


def run() -> None:
    from modules.license_guard import enforce_or_exit

    enforce_or_exit()
    from modules.flipbot_core import main

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        raise SystemExit(0) from None


if __name__ == "__main__":
    run()
