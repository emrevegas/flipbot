"""VegasBet — dev entry. Licensed customers use compiled flipbot_launcher (.so)."""
from modules.flipbot_core import FlipBot, COGS, main  # noqa: F401

if __name__ == "__main__":
    from modules.flipbot_launcher import run

    run()
