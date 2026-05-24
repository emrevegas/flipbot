import discord
from modules.constants import FOOTER_TEXT
from modules.database import *
from modules.player import Player
import random

class ServerData:
    def __init__(self, server_id):
        self.server_id = str(server_id)
        self.data = get_server_data() or {}
    
    def get_house_edge(self, key, default=None):
        return self.data.get(key, default)
    
    def set(self, key, value):
        self.data[key] = value
        set_server_data(self.data)

def create_error_embed(error_message):
    """Create a standardized error embed."""
    embed = discord.Embed(
        title="❌ Hata!",
        description=error_message,
        color=discord.Color.red()
    )
    embed.set_footer(text=FOOTER_TEXT.format(değişken="Hata"))
    return embed


def error_embed(msg: str) -> discord.Embed:
    """FlipBot-style compact error embed (alias for flip_utils.error_embed)."""
    from modules.flip_utils import error_embed as _error_embed
    return _error_embed(msg)


def success_embed(msg: str) -> discord.Embed:
    from modules.flip_utils import success_embed as _success_embed
    return _success_embed(msg)


def info_embed(title: str, msg: str) -> discord.Embed:
    from modules.flip_utils import info_embed as _info_embed
    return _info_embed(title, msg)


def fmt_pts(n: float) -> str:
    from modules.flip_utils import fmt_pts as _fmt_pts
    return _fmt_pts(n)

def create_warning_embed(warning_message):
    """Create a standardized warning embed."""
    embed = discord.Embed(
        title="⚠️ Uyarı!",
        description=warning_message,
        color=discord.Color.orange()
    )
    embed.set_footer(text=FOOTER_TEXT.format(değişken="Uyarı"))
    return embed

def create_success_embed(title, description):
    """Create a standardized success embed."""
    embed = discord.Embed(
        title=f"✅ {title}",
        description=description,
        color=discord.Color.green()
    )
    embed.set_footer(text=FOOTER_TEXT)
    return embed

def get_user_lang(user_id):
    """Get user's language preference from database."""
    from modules.database import get_user_data
    try:
        lang_data = get_user_data(int(user_id), "lang") or {}
        return lang_data.get("language", "en")
    except:
        return "en"

def format_balance(balance, mode=False):
    """Format the balance with the selected coin emoji and USD equivalent."""
    from modules.database import get_data

    # Get coin emoji from server settings
    server_data = get_data("server/server") or {}

    is_demo = mode and str(mode).lower() == "demo"
    if is_demo:
        coin_emoji = server_data.get("demo_coin_emoji", "<:mor_elmas:1183873215467110572>")
    else:
        coin_emoji = server_data.get("coin_emoji", "<:wl:1087846393722449990>")

    try:
        balance = int(balance)
        if balance == 0:
            base = f"0 {coin_emoji}"
        elif balance < 0:
            base = f"-{abs(balance):,} {coin_emoji}"
        else:
            base = f"{balance:,} {coin_emoji}"

        # Append USD equivalent for real-mode balances
        if not is_demo and balance != 0:
            try:
                from modules.database import get_data as _gd
                rates_data = _gd("server/exchange_rates") or {}
                rate = float(rates_data.get("coin_usd_rate", 0))
                if rate > 0:
                    usd = abs(balance) * rate
                    if usd >= 1000:
                        usd_str = f"${usd:,.0f}"
                    elif usd >= 0.01:
                        usd_str = f"${usd:,.2f}"
                    else:
                        usd_str = f"${usd:.4f}"
                    sign = "-" if balance < 0 else ""
                    base = f"{base} ({sign}{usd_str})"
            except Exception:
                pass

        return base
    except (ValueError, TypeError):
        return f"0 {coin_emoji}"

async def validate_user_permission(interaction: discord.Interaction) -> bool:
    """
    Validate if the user interacting with the button is the menu creator.
    Returns True if valid, otherwise sends a warning message and returns False.
    """
    user_menus = get_data("server/userMenus")
    menu_data = user_menus.get(str(interaction.message.id))
    print(f"User {interaction.user.id} tried to use a menu they don't own.")
    print(f"Menu data: {menu_data}")
    print(f"User data: {user_menus}")
    if not menu_data or menu_data["user_id"] != interaction.user.id:
        

        await interaction.response.send_message(
            embed=create_warning_embed("Bu menüyü kullanma yetkiniz yok."), ephemeral=True)
        return False
    return True

def gameEmbed(user_id):
    userBets = get_data("server/userBets")
    lastWins = get_data("server/lastWins")
    if str(user_id) not in lastWins:
        lastWins[str(user_id)] = {"win": "0", "mode": "real"}
        set_data("server/lastWins", lastWins)
    lastwin_mode = lastWins.get(str(user_id)).get("mode")
    SelectedGames = get_data("server/SelectedGames")
    GameInfos = get_data("server/GameInfos")
    bet_amount = userBets.get(str(user_id)).get("bet", 0)
    SelectedGame = SelectedGames.get(str(user_id), None)
    emojiOfGame = ge(GameInfos[SelectedGame]["emoji"])
    infoOfGame = GameInfos[SelectedGame]["info"]
    mode = userBets.get(str(user_id)).get("mode", "real")
    current_mode  = mode.replace("real", f"{ge('bgl')} Real Currency").replace("demo", f"{ge('mor_elmas')} Demo Balance")
    embed = discord.Embed(
        title=f"{emojiOfGame} {SelectedGame}",
        description=f"**{SelectedGame} Info**\n{infoOfGame}\n\n**Current Bet:** {format_balance(bet_amount, mode)}\n\n**Last win:** {format_balance(lastWins.get(str(user_id)).get('win', 0), lastwin_mode)}\n**Currency:**{current_mode}",
        color=discord.Color.blurple()
    )
    embed.set_footer(text=FOOTER_TEXT)
    return embed

def emojirate(value):
    playerNoMoji = ""
    value = str(value)
    for hit in value:
        if len(value) == 1:
            playerNoMoji += ge("n0")
            playerNoMoji += ge(f"n{hit}")
        else:
            playerNoMoji += ge(f"n{hit}")
    return playerNoMoji

def gameTable(block, playerHit, BotHit, result, avatar):
    sp = ge(block)
    playerHit = str(playerHit)
    PnoEmoji = emojirate(playerHit)
    BnoEmoji = emojirate(BotHit)
    bosluk = ge("bosluk")
    avatar = ge(avatar)
    roul = ge("roulette")
    botEmoji = ge("gt_bot")
    if result.lower().startswith("win"):
        arrow = ge("al")
    elif result.lower().startswith("lost"):
        arrow = ge("ark")
    else:
        arrow = ge("au")
    
    
    table = f"{sp}{sp}{sp}{sp}{sp}{sp}{sp}\n{sp}{PnoEmoji}{bosluk}{BnoEmoji}{sp}\n{sp}{avatar}{roul}{arrow}{roul}{botEmoji}{sp}\n{sp}{sp}{sp}{sp}{sp}{sp}{sp}"
    return table

def InsufficientEmbed(user_id) -> discord.Embed:
    player = Player(user_id)
    bets=get_data("server/userBets")
    bet = bets.get(player.uid, 0)
    need = int(bet) - player.balance
    embed = discord.Embed(
        title=f"{ge('carpi')} Insufficient Balance!",
        decription=f"You only have {format_balance(player.balance)} you need {format_balance(need)} to play this round!",
        color=discord.Color.red()
    )
    embed.set_footer(text=FOOTER_TEXT)
    return embed

IntegerEmbed = discord.Embed(
    title="Bet must be Integer",
    description="Sorry bet must be integer. Please use numbers eg. 500",
    color = discord.Color.red()
)

def checkMaxBet(bet):
    data = get_server_data()
    max_bet = data.get("maxBet", 50000)
    if int(bet) > int(max_bet):
        embed = discord.Embed(
            title="Max bet exceeded!",
            description=f"{ge('carpi')} Sorry the max bet amount is {format_balance(max_bet)}",
            color=discord.Color.red()
        )
        embed.set_footer(text=FOOTER_TEXT)
        return [True, embed]
    else:
        return [False]
    
def checkMinBet(bet):
    data = get_server_data()
    min_bet = data.get("minBet", 20)
    if int(bet) < int(min_bet):
        embed = discord.Embed(
            title="Minimum bet!",
            description=f"{ge('carpi')} Sorry the minimum bet amount is {format_balance(min_bet)}",
            color=discord.Color.red()
        )
        embed.set_footer(text=FOOTER_TEXT)
        return [True, embed]
    else:
        return [False]
    
def randomString(length=8):
    string = "ABCDEFGHIJKLMNOPRSTUVYZ"
    result = "".join(random.choice(string) for _ in range(length))
    return result

def check_inGame(user_id):
    inGames = get_data("server/inGames")
    if inGames.get(str(user_id), False):
        return True
    return False

