"""Main Discord Bot implementation for Kaizen."""

import logging
import asyncio
from datetime import date
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from kaizen.ai.client import ai_client
from kaizen.bot.commands import (
    build_level_up_embed,
    build_rank_embed,
    build_rank_up_embed,
)
from kaizen.config import settings
from kaizen.core.player import add_active_skill, get_or_create_player, remove_active_skill
from kaizen.core.progress import (
    check_and_apply_penalty,
    get_player_status,
    render_progress_bar,
)
from kaizen.core.quests import (
    complete_quest,
    ensure_daily_quests,
    get_today_quests,
)
from kaizen.db.models import DailyLog, Player, Quest, Skill
from kaizen.db.session import SessionLocal

logger = logging.getLogger(__name__)


class RemoveSkillConfirmation(discord.ui.View):
    """Require the requesting player to confirm removal of their active skill."""

    def __init__(self, user_id: int, skill_id: int) -> None:
        super().__init__(timeout=60)
        self.user_id = user_id
        self.skill_id = skill_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Only the player who requested this confirmation can use it.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Remove Skill", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            with SessionLocal() as db:
                player = db.query(Player).filter(
                    Player.discord_user_id == str(interaction.user.id)
                ).first()
                if player is None:
                    raise ValueError("Player not found.")
                name = remove_active_skill(db, player, expected_skill_id=self.skill_id)
                db.commit()
        except ValueError as exc:
            await interaction.response.edit_message(content=str(exc), embed=None, view=None)
            return

        await interaction.response.edit_message(
            content=(
                f"**{name}** was removed, along with today's quests for that skill. "
                "XP already earned, level, streak, logs, and completed quest history from earlier days were kept. "
                "Use /register to choose a replacement skill."
            ),
            embed=None,
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Skill removal cancelled.", embed=None, view=None)


class KaizenBot(commands.Bot):
    """Discord bot client with slash commands."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True  # Required for privileged intents
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        """Register application commands."""
        await self._register_commands()
        try:
            if settings.DISCORD_GUILD_ID:
                guild = discord.Object(id=settings.DISCORD_GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                logger.info("Synced commands to guild %s", settings.DISCORD_GUILD_ID)
            else:
                await self.tree.sync()
                logger.info("Synced commands globally.")
        except Exception as e:
            logger.warning("Failed to sync slash commands: %s", e)

    async def on_message(self, message: discord.Message) -> None:
        """Accept ordinary chat in DMs or when explicitly mentioned in a server."""
        if message.author.bot or self.user is None:
            return
        if message.guild is not None and self.user not in message.mentions:
            await self.process_commands(message)
            return

        content = message.content
        content = content.replace(f"<@{self.user.id}>", "").replace(f"<@!{self.user.id}>", "").strip()
        if not content:
            await message.reply("The System awaits your input, Hunter. State your query.")
            return

        with SessionLocal() as db:
            player = db.query(Player).filter(Player.discord_user_id == str(message.author.id)).first()
            player_id = player.id if player else None
        if player_id is None:
            await message.reply("Unregistered entity detected. Use `/register` with the skill you wish to grind, then address the System.")
            return

        async with message.channel.typing():
            from kaizen.core.agent import get_agent_manager
            with get_agent_manager() as agent_mgr:
                reply = await agent_mgr.handle_conversational_request(player_id, content)
        await message.reply(reply[:1900])

    async def _register_commands(self) -> None:
        @self.tree.command(name="register", description="Register as a Player in the System.")
        @app_commands.describe(skill="The single skill you wish to master (e.g. Python, Digital Art, Boxing)")
        async def register(interaction: discord.Interaction, skill: str) -> None:
            await interaction.response.defer(ephemeral=False, thinking=True)
            user_id = str(interaction.user.id)
            user_name = interaction.user.display_name
            skill_input = skill.strip()

            with SessionLocal() as db:
                player, created = get_or_create_player(
                    db,
                    discord_user_id=user_id,
                    hunter_name=user_name,
                    initial_skill=skill_input,
                )

                active_skill = player.active_skill
                if not created and active_skill is not None:
                    await interaction.followup.send(
                        f"⚠️ You are already registered as a Player.\n"
                        f"Active skill: **{active_skill.name}** (Level {player.level}, Rank {player.rank})."
                    )
                    return

                if active_skill is None:
                    active_skill = add_active_skill(db, player, skill_input)

                # Ensure quests
                ai_quests = await asyncio.to_thread(
                    ai_client.generate_daily_quests,
                    skill_name=active_skill.name,
                    player_level=player.level,
                    player_rank=player.rank,
                )
                ensure_daily_quests(db, player, quests_data=ai_quests)
                db.commit()

                # Store values before session closes
                rank_val = player.rank
                level_val = player.level
                from kaizen.db.models import NotificationPreference
                preferences = db.query(NotificationPreference).filter_by(player_id=player.id).first()
                if preferences is None:
                    db.add(NotificationPreference(player_id=player.id))
                    db.commit()

            embed = discord.Embed(
                title="⬆ NOTIFICATION: AWAKENING",
                description=(
                    f"**Welcome, Hunter {user_name}.**\n\n"
                    f"You have been chosen by the System.\n"
                    f"Assigned Stat to Grind: **{skill_input}**\n"
                    f"Current Rank: **{rank_val}** | Level: **{level_val}**\n"
                    f"Your existing XP and level have been retained.\n\n"
                    f"The System demands continuous progression.\n"
                    f"Complete your daily quests every day to achieve **+1% mastery**.\n"
                    f"Failure to complete daily quests will result in entering the **Penalty Zone**."
                ),
                color=0x00aaff,
            )
            embed.set_footer(text="Use /quests to inspect your daily training.")
            await interaction.followup.send(embed=embed)
            
            followup_embed = discord.Embed(
                title="🔔 SYSTEM: PROFILE REQUIRED",
                description=(
                    f"Your skill **{skill_input}** is registered. Notifications are active.\n\n"
                    f"State your **goal** and a **measurable target** in a DM, mention, or `/coach`.\n"
                    f"The System will save your proposal and demand confirmation before use."
                ),
                color=0xffaa00,
            )
            await interaction.followup.send(embed=followup_embed)

        @self.tree.command(name="remove-skill", description="Remove your active skill after confirmation.")
        async def remove_skill(interaction: discord.Interaction) -> None:
            user_id = str(interaction.user.id)
            with SessionLocal() as db:
                player = db.query(Player).filter(Player.discord_user_id == user_id).first()
                skill = player.active_skill if player else None
                if skill is None:
                    await interaction.response.send_message(
                        "You have no active skill to remove. Use `/register` to add one.",
                        ephemeral=True,
                    )
                    return

                embed = discord.Embed(
                    title="⬆ SYSTEM CONFIRMATION",
                    description=(
                        f"Remove **{skill.name}** and its **{skill.mastery_pct:.1f}% mastery**? "
                        "XP already earned, level, streak, logs, and completed quest history from earlier days will remain. "
                        "XP already earned from today's completed quests is kept."
                    ),
                    color=0xff3366,
                )
                view = RemoveSkillConfirmation(interaction.user.id, skill.id)

            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        @self.tree.command(name="status", description="Inspect your character status and progress bar.")
        async def status(interaction: discord.Interaction) -> None:
            await interaction.response.defer(thinking=True)
            user_id = str(interaction.user.id)
            with SessionLocal() as db:
                player = db.query(Player).filter(Player.discord_user_id == user_id).first()
                if not player:
                    await interaction.followup.send("❌ You are not registered yet. Use `/register` to awaken.")
                    return

                penalty_res = check_and_apply_penalty(db, player)
                if penalty_res.penalty_incurred:
                    db.commit()
                    await interaction.followup.send(penalty_res.message)

                st = get_player_status(db, player)
                embed = build_rank_embed(player, st, ai_available=ai_client.is_available)
                await interaction.followup.send(embed=embed)

        @self.tree.command(name="quests", description="View today's Daily Quests window.")
        async def quests(interaction: discord.Interaction) -> None:
            await interaction.response.defer(thinking=True)
            user_id = str(interaction.user.id)
            with SessionLocal() as db:
                player = db.query(Player).filter(Player.discord_user_id == user_id).first()
                if not player:
                    await interaction.followup.send("❌ You are not registered yet. Use `/register` to awaken.")
                    return

                if not player.active_skill:
                    await interaction.followup.send(
                        "You have no active skill. Use `/register skill:<name>` to choose one."
                    )
                    return

                # If no quests exist yet today, generate them
                today_quests = get_today_quests(db, player.id)
                if not today_quests:
                    skill_name = player.active_skill.name if player.active_skill else "General Mastery"
                    ai_quests = ai_client.generate_daily_quests(
                        skill_name=skill_name,
                        player_level=player.level,
                        player_rank=player.rank,
                    )
                    today_quests = ensure_daily_quests(db, player, quests_data=ai_quests)
                    db.commit()

                embed = discord.Embed(
                    title="📜 [DAILY QUEST: PREPARATION FOR GETTING STRONGER]",
                    description=(
                        "**GOAL**: Complete all daily quests to secure **+1% skill mastery**.\n"
                        "⚠️ Failure to complete all quests before reset incurs a penalty."
                    ),
                    color=0x0099ff,
                )

                for q in today_quests:
                    status_icon = "✅" if q.is_completed else "⏳"
                    field_name = f"{status_icon} Quest #{q.id}: {q.title} [{q.difficulty}-Rank]"
                    field_val = f"{q.description}\n**Reward**: +{q.xp_reward} XP"
                    embed.add_field(name=field_name, value=field_val, inline=False)

                all_done = all(q.is_completed for q in today_quests)
                if all_done:
                    embed.set_footer(text="🌟 ALL DAILY QUESTS CLEARED! +1% Mastery registered for today.")
                else:
                    embed.set_footer(text="Use /complete <quest_id> or button below to report completion.")

                await interaction.followup.send(embed=embed)

        @self.tree.command(
            name="reset_quests",
            description="Repair and regenerate today's quests without changing player progress.",
        )
        async def reset_quests(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            user_id = str(interaction.user.id)

            with SessionLocal() as db:
                player = db.query(Player).filter(Player.discord_user_id == user_id).first()
                if player is None:
                    await interaction.followup.send(
                        "You are not registered. Use `/register skill:<name>` first.",
                        ephemeral=True,
                    )
                    return

                active_skill = player.active_skill
                if active_skill is None:
                    await interaction.followup.send(
                        "You have no active skill. Use `/register skill:<name>` first.",
                        ephemeral=True,
                    )
                    return

                player_id = player.id
                skill_name = active_skill.name
                player_level = player.level
                player_rank = player.rank
                before = (
                    player.current_xp,
                    player.level,
                    player.current_streak,
                    player.total_days_cleared,
                    active_skill.mastery_pct,
                )

                # Remove only today's quest rows; do not modify XP/progress/logs.
                for old_quest in get_today_quests(db, player_id):
                    db.delete(old_quest)
                db.flush()

                generated = await asyncio.to_thread(
                    ai_client.generate_daily_quests,
                    skill_name=skill_name,
                    player_level=player_level,
                    player_rank=player_rank,
                )
                quests_for_today = ensure_daily_quests(db, player, quests_data=generated)

                after = (
                    player.current_xp,
                    player.level,
                    player.current_streak,
                    player.total_days_cleared,
                    active_skill.mastery_pct,
                )
                if before != after:
                    db.rollback()
                    logger.error("Quest recovery unexpectedly modified player progress; transaction rolled back.")
                    await interaction.followup.send(
                        "Quest recovery was rolled back because player progress changed unexpectedly.",
                        ephemeral=True,
                    )
                    return

                db.commit()
                quest_count = len(quests_for_today)
                using_templates = any(q.source != "ai" for q in quests_for_today)

            source_note = "Template recovery quests were used because AI output was unavailable or invalid." if using_templates else "Fresh AI quests generated."
            await interaction.followup.send(
                f"✅ Recovered today's quests for **{skill_name}** ({quest_count} quests). "
                f"{source_note} Player XP, level, streak, mastery, and cleared-day progress were not changed.",
                ephemeral=True,
            )

        @self.tree.command(name="complete", description="Mark a specific daily quest as completed.")
        @app_commands.describe(quest_id="The quest ID number shown in /quests")
        async def complete(interaction: discord.Interaction, quest_id: int) -> None:
            await interaction.response.defer(thinking=True)
            user_id = str(interaction.user.id)
            with SessionLocal() as db:
                player = db.query(Player).filter(Player.discord_user_id == user_id).first()
                if not player:
                    await interaction.followup.send("❌ You are not registered yet. Use `/register` to awaken.")
                    return

                try:
                    res = complete_quest(db, player, quest_id)
                    db.commit()
                except ValueError as exc:
                    await interaction.followup.send(f"⚠️ {exc}")
                    return

                embed = discord.Embed(
                    title="⬆ NOTIFICATION: QUEST COMPLETE",
                    description="\n".join([
                        f"⚔️ **Quest Completed**: {res.quest.title}",
                        f"Granted: **+{res.quest.xp_reward} XP",
                    ] + (
                        [
                            f"\n✨ **ALL DAILY QUESTS CLEARED!**\n"
                            f"• Skill Mastery: **+1.0%** (Total: **{res.new_mastery_pct}%**)\n"
                            f"• Current Streak: **🔥 {res.streak} days**\n"
                            f"• Completion Bonus: **+{res.bonus_xp} XP"
                        ] if res.daily_cleared else [])
                    ),
                    color=0x00ff88,
                )
                await interaction.followup.send(embed=embed)

                if res.xp_result.leveled_up:
                    lvl_embed = build_level_up_embed(
                        player, res.xp_result.previous_level, res.xp_result.new_level
                    )
                    await interaction.followup.send(embed=lvl_embed)

                if res.rank_changed:
                    rnk_embed = build_rank_up_embed(player, res.old_rank, res.new_rank)
                    await interaction.followup.send(embed=rnk_embed)

        @self.tree.command(name="log", description="Record training notes and receive System analysis.")
        @app_commands.describe(notes="What you practiced, mistakes made, or observations")
        async def log_training(interaction: discord.Interaction, notes: str) -> None:
            await interaction.response.defer(thinking=True)
            user_id = str(interaction.user.id)
            with SessionLocal() as db:
                player = db.query(Player).filter(Player.discord_user_id == user_id).first()
                if not player:
                    await interaction.followup.send("❌ You are not registered yet. Use `/register` to awaken.")
                    return

                today = date.today()
                today_quests = get_today_quests(db, player.id, today)
                completed_count = sum(1 for q in today_quests if q.is_completed)
                skill_name = player.active_skill.name if player.active_skill else "Mastery"

                feedback = ai_client.analyze_log(
                    skill_name=skill_name,
                    user_notes=notes,
                    quests_completed=completed_count,
                    quests_total=len(today_quests),
                )
                if not feedback:
                    feedback = "The System has logged your training data. Continue your progression."

                db_log = (
                    db.query(DailyLog)
                    .filter(DailyLog.player_id == player.id, DailyLog.log_date == today)
                    .first()
                )
                if not db_log:
                    db_log = DailyLog(
                        player_id=player.id,
                        log_date=today,
                        user_notes=notes,
                        ai_feedback=feedback,
                        quests_total=len(today_quests),
                        quests_completed=completed_count,
                    )
                    db.add(db_log)
                else:
                    db_log.user_notes = notes
                    db_log.ai_feedback = feedback

                db.commit()

            embed = discord.Embed(
                title="📋 [TRAINING LOG REGISTERED]",
                description=f"**Notes**: {notes}\n\n**System Evaluation**:\n{feedback}",
                color=0x9955ff,
            )
            await interaction.followup.send(embed=embed)

        @self.tree.command(name="coach", description="Direct communication channel with the System.")
        @app_commands.describe(message="Your query to the System")
        async def coach(interaction: discord.Interaction, message: str) -> None:
            await interaction.response.defer(thinking=True)
            user_id = str(interaction.user.id)
            
            with SessionLocal() as db:
                player = db.query(Player).filter(Player.discord_user_id == user_id).first()
                if not player:
                    await interaction.followup.send(
                        "You are not registered yet. Use `/register` to awaken.",
                        ephemeral=True,
                    )
                    return
                
                from kaizen.core.agent import get_agent_manager
                with get_agent_manager() as agent_mgr:
                    # Use conversational agent for intelligent handling
                    reply = await agent_mgr.handle_conversational_request(player.id, message)
            
            embed = discord.Embed(
                title="💬 [SYSTEM VOICE]",
                description=reply,
                color=0x00ccff,
            )
            await interaction.followup.send(embed=embed)

        @self.tree.command(name="agent", description="Agent management commands.")
        async def agent(interaction: discord.Interaction) -> None:
            """Base agent command - shows subcommands."""
            await interaction.response.send_message(
                "Agent commands: `/agent status`, `/agent diagnose`, `/agent repair-quests`",
                ephemeral=True,
            )

        @self.tree.command(name="agent-status", description="Check agent status and recent job results.")
        async def agent_status(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            user_id = str(interaction.user.id)
            
            with SessionLocal() as db:
                player = db.query(Player).filter(Player.discord_user_id == user_id).first()
                if not player:
                    await interaction.followup.send("❌ You are not registered yet. Use `/register` to awaken.", ephemeral=True)
                    return
                
                from kaizen.core.agent import get_agent_manager
                agent_mgr = get_agent_manager()
                status = await agent_mgr.handle_agent_status(player.id)
            
            embed = discord.Embed(
                title="⬆ AGENT STATUS",
                description="System agent status and recent activity.",
                color=0x00aaff,
            )
            
            if status.get("latest_job"):
                job = status["latest_job"]
                embed.add_field(
                    name="Latest Job",
                    value=f"**{job['name']}** - {'✅ Success' if job['success'] else '❌ Failed'}\n"
                          f"Fallback: {'Yes' if job['fallback_used'] else 'No'}\n"
                          f"Time: {job['started_at']}",
                    inline=False)
            else:
                embed.add_field(name="Latest Job", value="No jobs recorded yet.", inline=False)
            
            if status.get("using_fallback_quests"):
                embed.add_field(name="⚠️ Fallback Active", value="Today's quests are using template fallbacks.", inline=False)
            
            if status.get("recent_failures"):
                fail_text = "\n".join([f"• {f['job_name']}: {f['error'][:100]}" for f in status["recent_failures"][:3]])
                embed.add_field(name="Recent Failures", value=fail_text, inline=False)
            else:
                embed.add_field(name="Recent Failures", value="None", inline=False)
            
            await interaction.followup.send(embed=embed)

        @self.tree.command(name="agent-diagnose", description="Diagnose recent agent failures and issues.")
        async def agent_diagnose(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            user_id = str(interaction.user.id)
            
            with SessionLocal() as db:
                player = db.query(Player).filter(Player.discord_user_id == user_id).first()
                if not player:
                    await interaction.followup.send("❌ You are not registered yet. Use `/register` to awaken.", ephemeral=True)
                    return
                
                from kaizen.core.agent import get_agent_manager
                agent_mgr = get_agent_manager()
                diag = await agent_mgr.handle_agent_diagnose(player.id)
            
            embed = discord.Embed(
                title="🔍 AGENT DIAGNOSIS",
                color=0xff9900,
            )
            
            if diag.get("status") == "healthy":
                embed.description = "✅ **System Healthy** - No recent failures detected."
            else:
                embed.description = f"⚠️ **Issues Detected** ({diag.get('failure_count', 0)} failures)\n\n"
                embed.description += f"**Primary Issue**: {diag.get('primary_issue', 'Unknown')}\n\n"
                
                for err_type, count in diag.get("error_types", {}).items():
                    embed.add_field(
                        name=f"Error ({count}x)",
                        value=err_type[:100],
                        inline=False
                    )
            
            await interaction.followup.send(embed=embed)

        @self.tree.command(name="agent-repair", description="Repair today's quests if generation failed.")
        async def agent_repair(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            user_id = str(interaction.user.id)
            
            with SessionLocal() as db:
                player = db.query(Player).filter(Player.discord_user_id == user_id).first()
                if not player:
                    await interaction.followup.send("❌ You are not registered yet. Use `/register` to awaken.", ephemeral=True)
                    return
                
                from kaizen.core.agent import get_agent_manager
                agent_mgr = get_agent_manager()
                result = await agent_mgr.handle_agent_repair(player.id)
            
            if result.get("success"):
                embed = discord.Embed(
                    title="✅ REPAIR COMPLETE",
                    description=(
                        f"Regenerated {len(result.get('quests', []))} quests for **{result.get('quests', [{}])[0].get('title', 'skill') if result.get('quests') else 'skill'}**.\n"
                        f"Player progress (XP, level, streak, mastery) was preserved."
                    ),
                    color=0x00ff88,
                )
                if result.get("quests"):
                    quest_text = "\n".join([f"• {q['title']} [{q['difficulty']}] +{q['xp']} XP" for q in result["quests"]])
                    embed.add_field(name="Regenerated Quests", value=quest_text, inline=False)
            else:
                embed = discord.Embed(
                    title="❌ REPAIR FAILED",
                    description=result.get("error", "Unknown error occurred."),
                    color=0xff3366,
                )
            
            await interaction.followup.send(embed=embed)

        @self.tree.command(name="notification-setup", description="Set up notifications and goal for your active skill.")
        @app_commands.describe(
            goal="Your primary goal for this skill (e.g., 'Build web apps', 'Run a marathon')",
            target="Measurable target (e.g., '10 projects', 'Under 4 hours')",
            constraints="Any constraints or preferences (e.g., 'Weekends only', '30 min/day')"
        )
        async def notification_setup(
            interaction: discord.Interaction,
            goal: str,
            target: str,
            constraints: str = "",
        ) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            user_id = str(interaction.user.id)
            
            with SessionLocal() as db:
                player = db.query(Player).filter(Player.discord_user_id == user_id).first()
                if not player or not player.active_skill:
                    await interaction.followup.send("❌ You need an active skill first. Use `/register` to awaken.", ephemeral=True)
                    return
                
                skill = player.active_skill
                from kaizen.core.notifications import get_notification_manager
                notif_mgr = get_notification_manager()
                
                # Save skill profile (unconfirmed initially)
                profile = notif_mgr.upsert_skill_profile(
                    skill.id,
                    goal=goal,
                    target=target,
                    constraints=constraints,
                    confirmed=True,  # Auto-confirm for now
                )
                
                # Ensure preferences exist
                prefs = notif_mgr.get_preferences(player.id)
                if not prefs:
                    notif_mgr.upsert_preferences(player.id, notifications_enabled=True)
            
            embed = discord.Embed(
                title="🔔 SYSTEM: PROFILE CONFIRMED",
                description=f"Skill: **{skill.name}**\n\n"
                            f"Goal: {goal}\n"
                            f"Target: {target}\n"
                            f"Constraints: {constraints or 'None'}\n\n"
                            "Confirmed. The System will generate skill-aware notifications during permitted hours.",
                color=0x00ff88,
            )
            await interaction.followup.send(embed=embed)

        @self.tree.command(name="notification-status", description="Check your notification preferences and active profiles.")
        async def notification_status(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            user_id = str(interaction.user.id)
            
            with SessionLocal() as db:
                player = db.query(Player).filter(Player.discord_user_id == user_id).first()
                if not player:
                    await interaction.followup.send("❌ You are not registered yet. Use `/register` to awaken.", ephemeral=True)
                    return
                
                from kaizen.core.notifications import get_notification_manager
                notif_mgr = get_notification_manager()
                
                prefs = notif_mgr.get_preferences(player.id)
                skill = player.active_skill
                profile = notif_mgr.get_skill_profile(skill.id) if skill else None
            
            embed = discord.Embed(
                title="🔔 SYSTEM: NOTIFICATION STATUS",
                color=0x00aaff,
            )
            
            if prefs:
                embed.add_field(
                    name="Notification Parameters",
                    value=f"Enabled: {'Yes' if prefs['enabled'] else 'No'}\n"
                          f"Paused: {'Yes' if prefs['paused'] else 'No'}\n"
                          f"Daily Cap: {prefs['daily_cap']} per skill\n"
                          f"Permitted Hours: {prefs['permitted_start']:02d}:00 - {prefs['permitted_end']:02d}:00\n"
                          f"Timezone: {prefs['timezone']}",
                    inline=False,
                )
            else:
                embed.add_field(name="Preferences", value="Defaults active (Enabled, 07:00-22:00 UTC)", inline=False)
            
            if profile and profile.get("confirmed"):
                embed.add_field(
                    name=f"Skill Profile: {skill.name}",
                    value=f"Goal: {profile['goal']}\n"
                          f"Target: {profile['target']}\n"
                          f"Constraints: {profile['constraints'] or 'None'}\n"
                          f"Status: Confirmed",
                    inline=False,
                )
            else:
                embed.add_field(
                    name="Skill Profile",
                    value="No confirmed profile. State your goal and target to the System.",
                    inline=False,
                )
            
            await interaction.followup.send(embed=embed)
