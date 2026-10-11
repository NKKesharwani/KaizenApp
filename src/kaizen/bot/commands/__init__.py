"""Discord slash command views and embed builders for Kaizen: The System."""

import discord
from discord.ui import View, Button

from kaizen.core.progress import render_progress_bar


class QuestCompleteView(View):
    """Interactive view with buttons to complete each of today's quests."""

    def __init__(self, quests: list, timeout: float = 60.0) -> None:
        super().__init__(timeout=timeout)
        self.quests = quests
        for q in quests:
            label = f"#{q.id} {q.title[:35]}"
            button = Button(
                label=label,
                style=discord.ButtonStyle.success if not q.is_completed else discord.ButtonStyle.secondary,
                custom_id=f"complete_{q.id}",
                disabled=q.is_completed,
            )
            button.callback = self._make_callback(q.id)
            self.add_item(button)

    async def _make_callback(self, quest_id: int):
        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            await interaction.followup.send(
                f"✅ Quest #{quest_id} completed. The System acknowledges your progress.",
                ephemeral=True,
            )
        return callback


def build_rank_embed(player, status, ai_available: bool = False) -> discord.Embed:
    """Build a Solo Leveling-style rank card embed."""
    embed = discord.Embed(
        title="⬆ NOTIFICATION: PLAYER STATUS",
        description=f"**{status.hunter_name}** — The System monitors your progress.",
        color=0x00aaff,
    )
    embed.set_author(name="SYSTEM", icon_url="https://cdn.discordapp.com/emojis/987654321/robot.png")
    embed.add_field(name="Rank", value=f"**{status.rank}**", inline=True)
    embed.add_field(name="Level", value=f"**{status.level}**", inline=True)
    embed.add_field(name="Skill", value=f"`{status.skill_name}`", inline=True)
    embed.add_field(name="Mastery", value=render_progress_bar(status.mastery_pct), inline=False)
    embed.add_field(name="XP", value=f"{status.current_xp} / {status.xp_needed} ({render_progress_bar(status.xp_progress_pct, length=10)})", inline=False)
    embed.add_field(name="Streak", value=f"🔥 {status.streak} days (Best: {status.max_streak})", inline=True)
    embed.add_field(name="Days Cleared", value=str(status.days_cleared), inline=True)
    embed.add_field(name="Quests Done", value=f"{status.today_quests_completed}/{status.today_quests_total}", inline=True)
    embed.set_footer(text="Kaizen: The System" + (" | AI Coach Active" if ai_available else " | Fallback Mode"))
    return embed


def build_level_up_embed(player, old_level: int, new_level: int) -> discord.Embed:
    """Build a LEVEL UP notification embed."""
    embed = discord.Embed(
        title="🔥 LEVEL UP!",
        description=f"**{player.hunter_name}** has leveled up from Level {old_level} to **Level {new_level}**.",
        color=0xffcc00,
    )
    embed.set_footer(text="The System continues to evolve you.")
    return embed


def build_rank_up_embed(player, old_rank: str, new_rank: str) -> discord.Embed:
    """Build a rank promotion embed."""
    embed = discord.Embed(
        title="⭐ RANK PROMOTION",
        description=f"**{player.hunter_name}** has ascended from Rank **{old_rank}** to **{new_rank}**!",
        color=0xff9900,
    )
    embed.set_footer(text="A new threshold has been unlocked.")
    return embed