"""Private thread system: .thread create/add/remove/close/info"""
from __future__ import annotations

import discord
from discord.ext import commands

from database import db
from modules import flip_utils as utils


def _thread_tag(user_id: int) -> str:
    return f"priv-{user_id}"


def _thread_matches(thread: discord.Thread, user_id: int) -> bool:
    if getattr(thread, "archived", False):
        return False
    tag = _thread_tag(user_id)
    closed = f"closed-{tag}"
    name = thread.name or ""
    if closed in name:
        return False
    return name.endswith(tag) or tag in name


async def _resolve_thread(guild: discord.Guild, thread: discord.Thread) -> discord.Thread | None:
    """Return thread if it still exists on Discord; None if deleted/stale cache."""
    try:
        ch = guild.get_thread(thread.id)
        if ch is None:
            ch = await guild.fetch_channel(thread.id)
        if isinstance(ch, discord.Thread) and not ch.archived:
            return ch
    except discord.NotFound:
        pass
    except discord.HTTPException:
        pass
    return None


async def _get_user_thread(guild: discord.Guild, user_id: int) -> discord.Thread | None:
    """Find the user's active private thread (skips deleted/stale cache entries)."""
    seen: set[int] = set()
    candidates: list[discord.Thread] = []

    for thread in guild.threads:
        tid = thread.id
        if tid in seen:
            continue
        seen.add(tid)
        if _thread_matches(thread, user_id):
            candidates.append(thread)

    try:
        active = await guild.active_threads()
        for thread in active.threads:
            tid = thread.id
            if tid in seen:
                continue
            seen.add(tid)
            if _thread_matches(thread, user_id):
                candidates.append(thread)
    except Exception:
        pass

    for thread in candidates:
        resolved = await _resolve_thread(guild, thread)
        if resolved:
            return resolved
    return None


async def _delete_user_thread(thread: discord.Thread) -> bool:
    """Permanently delete the private thread. Returns True if deleted or already gone."""
    try:
        await thread.delete()
        return True
    except discord.NotFound:
        return True


def _reply_channel(ctx: commands.Context, thread: discord.Thread) -> discord.abc.Messageable | None:
    """Channel to post command feedback (parent channel if cmd was run inside the thread)."""
    if isinstance(ctx.channel, discord.Thread) and ctx.channel.id == thread.id:
        return ctx.guild.get_channel(thread.parent_id) if ctx.guild else None
    return ctx.channel


async def _send_reply(ctx: commands.Context, thread: discord.Thread, *, embed: discord.Embed) -> None:
    ch = _reply_channel(ctx, thread)
    if ch is not None:
        ref = ctx.message.to_reference(fail_if_not_exists=False) if ctx.message else None
        await ch.send(embed=embed, reference=ref, mention_author=True)


class Threads(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.group(name="thread", aliases=["t"], invoke_without_command=True)
    async def thread_group(self, ctx: commands.Context):
        """Private thread management.\n.thread create / add / remove / close / info"""
        thread = await _get_user_thread(ctx.guild, ctx.author.id)
        if not thread:
            embed = discord.Embed(
                title="🧵 Private Threads",
                description=(
                    "You don't have a private thread yet.\n\n"
                    "**Commands:**\n"
                    "`.thread create [name]` — create your thread\n"
                    "`.thread add @user` — invite someone\n"
                    "`.thread remove @user` — remove someone\n"
                    "`.thread close` — delete your thread\n"
                    "`.thread info` — show thread details"
                ),
                color=0x5865F2,
            )
            return await ctx.send(embed=embed)

        await ctx.send(embed=discord.Embed(
            description=f"🧵 Your thread: {thread.mention}",
            color=0x5865F2,
        ))

    # ── create ─────────────────────────────────────────────────────────────────

    @thread_group.command(name="create")
    async def thread_create(self, ctx: commands.Context, *, name: str = ""):
        """Create your private thread. .thread create [name]"""
        if not ctx.guild:
            return await ctx.send(embed=utils.error_embed("Server only."))

        existing = await _get_user_thread(ctx.guild, ctx.author.id)
        if existing:
            return await ctx.send(embed=utils.error_embed(
                f"You already have a thread: {existing.mention}\n"
                "Use `.thread close` to delete it first."
            ))

        tag = _thread_tag(ctx.author.id)
        thread_name = f"{name or ctx.author.display_name} • {tag}"[:100]

        try:
            thread = await ctx.channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.private_thread,
                invitable=False,
                reason=f"Private thread for {ctx.author}",
            )
        except discord.Forbidden:
            return await ctx.send(embed=utils.error_embed(
                "I need **Create Private Threads** permission in this channel."
            ))
        except discord.HTTPException as e:
            return await ctx.send(embed=utils.error_embed(f"Failed to create thread: {e}"))

        await thread.add_user(ctx.author)

        embed = discord.Embed(
            title="🧵 Thread Created",
            description=(
                f"Your private thread: {thread.mention}\n\n"
                "• `.thread add @user` — invite someone\n"
                "• `.thread remove @user` — remove someone\n"
                "• `.thread close` — delete when done"
            ),
            color=0x2ECC71,
        )
        await ctx.send(embed=embed)
        await thread.send(
            embed=discord.Embed(
                description=f"👋 Welcome {ctx.author.mention}! This is your private thread.",
                color=0x5865F2,
            )
        )

    # ── add ────────────────────────────────────────────────────────────────────

    @thread_group.command(name="add")
    async def thread_add(self, ctx: commands.Context, member: discord.Member):
        """Add a member to your thread. .thread add @user"""
        if not ctx.guild:
            return await ctx.send(embed=utils.error_embed("Server only."))

        thread = await _get_user_thread(ctx.guild, ctx.author.id)
        if not thread:
            return await ctx.send(embed=utils.error_embed(
                "You don't have a private thread. Use `.thread create` first."
            ))
        if member.id == ctx.author.id:
            return await ctx.send(embed=utils.error_embed("You're already in your own thread."))

        try:
            await thread.add_user(member)
        except discord.HTTPException as e:
            return await ctx.send(embed=utils.error_embed(f"Failed to add user: {e}"))

        await ctx.send(embed=discord.Embed(
            description=f"✅ Added {member.mention} to {thread.mention}",
            color=0x2ECC71,
        ))
        try:
            await thread.send(embed=discord.Embed(
                description=f"👋 {member.mention} was added by {ctx.author.mention}.",
                color=0x5865F2,
            ))
        except Exception:
            pass

    # ── remove ─────────────────────────────────────────────────────────────────

    @thread_group.command(name="remove", aliases=["kick"])
    async def thread_remove(self, ctx: commands.Context, member: discord.Member):
        """Remove a member from your thread. .thread remove @user"""
        if not ctx.guild:
            return await ctx.send(embed=utils.error_embed("Server only."))
        if member.id == ctx.author.id:
            return await ctx.send(embed=utils.error_embed("Use `.thread close` to delete your own thread."))

        thread = await _get_user_thread(ctx.guild, ctx.author.id)
        if not thread:
            return await ctx.send(embed=utils.error_embed("You don't have a private thread."))

        try:
            await thread.remove_user(member)
        except discord.HTTPException as e:
            return await ctx.send(embed=utils.error_embed(f"Failed to remove user: {e}"))

        await ctx.send(embed=discord.Embed(
            description=f"✅ Removed {member.mention} from {thread.mention}",
            color=0x2ECC71,
        ))

    # ── close ──────────────────────────────────────────────────────────────────

    @thread_group.command(name="close", aliases=["archive", "delete"])
    async def thread_close(self, ctx: commands.Context):
        """Delete your private thread. .thread close"""
        if not ctx.guild:
            return await ctx.send(embed=utils.error_embed("Server only."))

        thread = await _get_user_thread(ctx.guild, ctx.author.id)
        if not thread:
            return await ctx.send(embed=utils.error_embed("You don't have an active thread."))

        try:
            await _delete_user_thread(thread)
        except discord.HTTPException as e:
            await _send_reply(ctx, thread, embed=utils.error_embed(f"Failed to delete thread: {e}"))
            return

        await _send_reply(
            ctx, thread,
            embed=utils.success_embed(
                "Your thread has been deleted. You can create a new one with `.thread create`."
            ),
        )

    # ── info ───────────────────────────────────────────────────────────────────

    @thread_group.command(name="info")
    async def thread_info(self, ctx: commands.Context):
        """Show info about your thread. .thread info"""
        if not ctx.guild:
            return await ctx.send(embed=utils.error_embed("Server only."))

        thread = await _get_user_thread(ctx.guild, ctx.author.id)
        if not thread:
            return await ctx.send(embed=utils.error_embed(
                "No active thread. Use `.thread create` to open one."
            ))

        members = thread.members
        embed = discord.Embed(title="🧵 Thread Info", color=0x5865F2)
        embed.add_field(name="Thread", value=thread.mention, inline=True)
        embed.add_field(name="Channel", value=f"<#{thread.parent_id}>", inline=True)
        embed.add_field(name="Members", value=str(len(members)) if members else "—", inline=True)
        embed.add_field(name="Created", value=f"<t:{int(thread.created_at.timestamp())}:R>", inline=True)
        embed.add_field(name="Messages", value=str(thread.message_count or 0), inline=True)
        await ctx.send(embed=embed)

    # ── Admin: force-close any thread ─────────────────────────────────────────

    @thread_group.command(name="forceclose")
    async def thread_forceclose(self, ctx: commands.Context, member: discord.Member):
        """Admin: force-archive another user's thread. .thread forceclose @user"""
        if not utils.is_admin(ctx):
            return await ctx.send(embed=utils.error_embed("Admins only."))
        if not ctx.guild:
            return await ctx.send(embed=utils.error_embed("Server only."))

        thread = await _get_user_thread(ctx.guild, member.id)
        if not thread:
            return await ctx.send(embed=utils.error_embed(f"{member.display_name} has no active thread."))

        try:
            await _delete_user_thread(thread)
        except discord.HTTPException as e:
            await _send_reply(ctx, thread, embed=utils.error_embed(f"Failed to delete thread: {e}"))
            return

        await _send_reply(
            ctx, thread,
            embed=utils.success_embed(f"Deleted {member.mention}'s thread."),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Threads(bot))
