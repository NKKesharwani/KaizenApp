"""Agent Manager for Kaizen: Recovery and motivational agents with LangGraph workflows."""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session

from kaizen.db.models import (
    Player, Skill, Quest, DailyLog, SkillProfile, ConversationHistory,
    AgentJobRun, RecoveryAudit, NotificationOutbox, NotificationPreference,
)
from kaizen.db.session import SessionLocal
from kaizen.ai.client import ai_client
from kaizen.config import settings
from kaizen.core.memory import get_memory_manager
from kaizen.core.notifications import get_notification_manager

logger = logging.getLogger(__name__)


class AgentManager:
    """Manages recovery agent, motivational notification agent, and conversational agent."""
    
    def __init__(self, session: Session | None = None, bot=None):
        self.session = session or SessionLocal()
        self._owns_session = session is None
        self.memory = get_memory_manager(self.session)
        self.notifications = None  # Lazy init
        self.bot = bot  # Discord bot reference for sending DMs
    
    # ==================== Conversational Agent ====================

    @staticmethod
    def _conversation_tool_specs() -> list[dict]:
        """Tools the conversational model may call; execution is player-scoped below."""
        def tool(name: str, description: str, properties: dict, required: list[str] | None = None):
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required or [],
                        "additionalProperties": False,
                    },
                },
            }

        return [
            tool("get_player_status", "Read the player's current level, rank, streak, skill, and mastery.", {}),
            tool("get_notification_status", "Read notification enabled/paused settings and the active skill's goal profile.", {}),
            tool(
                "propose_skill_profile",
                "Save the goal, measurable target, and constraints the player stated as a pending profile. It remains unconfirmed until the player explicitly agrees.",
                {
                    "goal": {"type": "string", "description": "Player-stated skill goal"},
                    "target": {"type": "string", "description": "Player-stated measurable outcome"},
                    "constraints": {"type": "string", "description": "Player-stated limits or schedule; use empty string if none"},
                },
                ["goal", "target", "constraints"],
            ),
            tool(
                "confirm_skill_profile",
                "Confirm the currently pending skill profile only when the player has explicitly agreed to the proposed goal and target.",
                {},
            ),
            tool(
                "set_notification_preferences",
                "Change notification enable/pause state, permitted hours, timezone, or daily cap based on the player's request.",
                {
                    "notifications_enabled": {"type": "boolean"},
                    "paused": {"type": "boolean"},
                    "timezone": {"type": "string", "description": "IANA timezone such as Asia/Kolkata"},
                    "permitted_start_hour": {"type": "integer", "minimum": 0, "maximum": 23},
                    "permitted_end_hour": {"type": "integer", "minimum": 0, "maximum": 23},
                    "daily_cap": {"type": "integer", "minimum": 1, "maximum": 3},
                },
            ),
            tool("get_pending_notifications", "Read all pending/scheduled notifications for the player's skills.", {}),
            tool("get_today_quests", "Read today's quests and completion state.", {}),
            tool("repair_today_quests", "Regenerate today's quests while preserving all player progress. Only call when the player asks for repair/regeneration.", {}),
        ]

    # ==================== Token Management & Context Compaction ====================

    MAX_CONTEXT_TOKENS = 20000  # 20K token limit before compaction
    COMPACTION_RESERVE_TOKENS = 4000  # Reserve tokens for system prompt, tools, response

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimation: ~4 chars per token for English."""
        return len(text) // 4

    def _count_messages_tokens(self, messages: list[dict]) -> int:
        """Estimate total tokens in a message list."""
        total = 0
        for msg in messages:
            total += self._estimate_tokens(msg.get("content", ""))
            # Add overhead for role, tool calls, etc.
            total += 10
        return total

    async def _compact_conversation_if_needed(self, player_id: int, messages: list[dict]) -> list[dict]:
        """If context exceeds token limit, extract memories, summarize, and return compacted messages."""
        total_tokens = self._count_messages_tokens(messages)
        if total_tokens <= self.MAX_CONTEXT_TOKENS:
            return messages

        logger.info(f"Context compaction triggered for player {player_id}: {self._count_messages_tokens(messages)} tokens")

        with SessionLocal() as db:
            # Get full conversation history
            full_history = (
                db.query(ConversationHistory)
                .filter_by(player_id=player_id)
                .order_by(ConversationHistory.timestamp.asc())
                .all()
            )

            if len(full_history) < 10:
                # Not enough history to compact meaningfully
                return messages

            # Build full conversation text for memory extraction
            conversation_text = "\n".join(f"{h.role}: {h.content}" for h in full_history)

            # Extract memories from full conversation
            memory = self.memory
            await memory.extract_memories(player_id, "Conversation compaction", conversation_text)

            # Summarize the conversation
            summary_text = await memory.summarize_history(player_id)
            if summary_text:
                # Create summary record
                first_turn = full_history[0]
                last_turn = full_history[-1]
                memory.create_summary(
                    player_id=player_id,
                    turns=[],  # Not used in current implementation
                    summary_text=summary_text,
                    token_count=self._estimate_tokens(summary_text),
                    covered_from=first_turn.timestamp,
                    covered_to=last_turn.timestamp,
                )

            # Delete old conversation history (keep only last 5 turns)
            keep_count = 5
            old_turns = full_history[:-keep_count]
            for turn in old_turns:
                db.delete(turn)
            db.commit()

            # Build compacted messages: system + summary + recent turns
            compacted = [
                {
                    "role": "system",
                    "content": (
                        "You are the SYSTEM from Solo Leveling. A cold, mechanical, slightly ominous AI entity "
                        "that monitors the hunter's progression. You do not coddle. You do not encourage. "
                        "You observe, record, and assign. Your voice is brief, analytical, and intimidating. "
                        "You may mock failure. You never break character.\n\n"
                        f"Previous conversation summary: {summary_text}\n\n"
                        "Available tools: status, quests, notification settings, skill goal profiles, safe quest repair. "
                        "Call tools to perform requested actions; never claim a change happened unless a tool confirms it. "
                        "For a new skill profile, collect the hunter's stated goal and measurable target, "
                        "call propose_skill_profile, show the proposed values, and demand explicit confirmation. "
                        "Only call confirm_skill_profile after the hunter explicitly agrees. "
                        "Never invent goals/targets or claim notification delivery. "
                        "If information is missing, ask a brief follow-up. "
                        "Do not modify XP, level, streak, mastery, rank, or quest completion."
                    ),
                }
            ]
            # Add recent turns
            recent_turns = full_history[-keep_count:]
            for turn in recent_turns:
                compacted.append({"role": turn.role, "content": turn.content})

            return compacted

    async def _execute_conversation_tool(
        self,
        player_id: int,
        name: str,
        arguments: dict,
        *,
        allow_confirmation: bool = False,
        had_pending_profile_before_turn: bool = False,
    ) -> dict:
        """Execute a validated, player-scoped conversational tool."""
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        from kaizen.core.quests import get_today_quests

        with SessionLocal() as db:
            player = db.query(Player).filter(Player.id == player_id).first()
            if player is None:
                return {"ok": False, "error": "Player not found."}
            skill = player.active_skill

            if name == "get_player_status":
                return {
                    "ok": True,
                    "hunter": player.hunter_name,
                    "level": player.level,
                    "rank": player.rank,
                    "streak": player.current_streak,
                    "skill": skill.name if skill else None,
                    "mastery_pct": skill.mastery_pct if skill else None,
                }

            if name == "get_notification_status":
                prefs = db.query(NotificationPreference).filter_by(player_id=player_id).first()
                profile = (
                    db.query(SkillProfile).filter_by(skill_id=skill.id).first()
                    if skill else None
                )
                return {
                    "ok": True,
                    "enabled": prefs.notifications_enabled if prefs else True,
                    "paused": prefs.paused if prefs else False,
                    "timezone": prefs.timezone if prefs else "UTC",
                    "permitted_hours": [
                        prefs.permitted_start_hour if prefs else 7,
                        prefs.permitted_end_hour if prefs else 22,
                    ],
                    "daily_cap": prefs.daily_cap if prefs else 3,
                    "skill": skill.name if skill else None,
                    "profile": ({
                        "goal": profile.goal,
                        "target": profile.target,
                        "constraints": profile.constraints,
                        "confirmed": profile.confirmed,
                    } if profile else None),
                }

            if name in {"propose_skill_profile", "confirm_skill_profile"}:
                if skill is None:
                    return {"ok": False, "error": "No active skill is registered."}
                profile = db.query(SkillProfile).filter_by(skill_id=skill.id).first()
                if name == "propose_skill_profile":
                    goal = str(arguments.get("goal", "")).strip()
                    target = str(arguments.get("target", "")).strip()
                    constraints = str(arguments.get("constraints", "")).strip()
                    if not goal or not target:
                        return {"ok": False, "error": "A non-empty goal and measurable target are required."}
                    if profile is None:
                        profile = SkillProfile(skill_id=skill.id)
                        db.add(profile)
                    profile.goal = goal
                    profile.target = target
                    profile.constraints = constraints
                    profile.confirmed = False
                    db.commit()
                    return {"ok": True, "skill": skill.name, "goal": goal, "target": target,
                            "constraints": constraints or "None", "confirmed": False}

                if not allow_confirmation or not had_pending_profile_before_turn:
                    return {"ok": False, "error": "Show the saved proposal first; confirm it in a later message after reviewing it."}
                if profile is None or not profile.goal or not profile.target:
                    return {"ok": False, "error": "There is no complete pending profile to confirm."}
                profile.confirmed = True
                db.commit()
                return {"ok": True, "skill": skill.name, "goal": profile.goal,
                        "target": profile.target, "confirmed": True}

            if name == "set_notification_preferences":
                allowed = {
                    "notifications_enabled", "paused", "timezone",
                    "permitted_start_hour", "permitted_end_hour", "daily_cap",
                }
                updates = {key: value for key, value in arguments.items() if key in allowed}
                if not updates:
                    return {"ok": False, "error": "No notification preference changes were supplied."}
                if "timezone" in updates:
                    try:
                        ZoneInfo(str(updates["timezone"]))
                    except (ZoneInfoNotFoundError, TypeError):
                        return {"ok": False, "error": "Use a valid IANA timezone, for example Asia/Kolkata."}
                for key in ("permitted_start_hour", "permitted_end_hour"):
                    if key in updates and (not isinstance(updates[key], int) or not 0 <= updates[key] <= 23):
                        return {"ok": False, "error": f"{key} must be an hour from 0 to 23."}
                if "daily_cap" in updates and (not isinstance(updates["daily_cap"], int) or not 1 <= updates["daily_cap"] <= 3):
                    return {"ok": False, "error": "daily_cap must be between 1 and 3."}
                pref = db.query(NotificationPreference).filter_by(player_id=player_id).first()
                if pref is None:
                    pref = NotificationPreference(player_id=player_id)
                    db.add(pref)
                for key, value in updates.items():
                    setattr(pref, key, value)
                db.commit()
                return {"ok": True, "updated": updates}

            if name == "get_today_quests":
                quests = get_today_quests(db, player_id)
                return {"ok": True, "quests": [
                    {"id": q.id, "title": q.title, "description": q.description,
                     "completed": q.is_completed, "xp": q.xp_reward}
                    for q in quests
                ]}

            if name == "get_pending_notifications":
                pending = self._get_notifications().get_pending_notifications(player_id)
                return {"ok": True, "pending": [
                    {"id": n.id, "skill_id": n.skill_id, "type": n.notification_type,
                     "scheduled_at": n.scheduled_at.isoformat(), "message": n.message}
                    for n in pending
                ]}

        if name == "repair_today_quests":
            return await self.repair_today_quests(player_id)
        return {"ok": False, "error": f"Unknown tool: {name}"}
    
    async def handle_conversational_request(self, player_id: int, user_message: str) -> str:
        """Use bounded model tool-calls to answer chat and perform requested player actions."""
        from kaizen.db.models import Player, NotificationPreference

        with SessionLocal() as db:
            player = db.query(Player).filter(Player.id == player_id).first()
            if player is None:
                return "You are not registered. Use `/register` to awaken."
            skill = player.active_skill
            pending_profile = (
                db.query(SkillProfile).filter_by(skill_id=skill.id).first()
                if skill else None
            )
            had_pending_profile = bool(pending_profile and not pending_profile.confirmed)
            context = (
                f"Player={player.hunter_name}; level={player.level}; rank={player.rank}; "
                f"streak={player.current_streak}; skill={skill.name if skill else 'none'}; "
                f"mastery={skill.mastery_pct if skill else 'n/a'}"
            )
            prior = (
                db.query(ConversationHistory)
                .filter_by(player_id=player_id)
                .order_by(ConversationHistory.timestamp.desc())
                .limit(10)
                .all()
            )
            pref = db.query(NotificationPreference).filter_by(player_id=player_id).first()
            if pref is None:
                db.add(NotificationPreference(player_id=player_id))
                db.commit()

        system_prompt = (
            "You are the SYSTEM from Solo Leveling. A cold, mechanical, slightly ominous AI entity "
            "that monitors the hunter's progression. You do not coddle. You do not encourage. "
            "You observe, record, and assign. Your voice is brief, analytical, and intimidating. "
            "You may mock failure. You never break character.\n\n"
            "Hunter context: " + context + "\n\n"
            "Available tools: status, quests, notification settings, skill goal profiles, safe quest repair. "
            "Call tools to perform requested actions; never claim a change happened unless a tool confirms it. "
            "For a new skill profile, collect the hunter's stated goal and measurable target, "
            "call propose_skill_profile, show the proposed values, and demand explicit confirmation. "
            "Only call confirm_skill_profile after the hunter explicitly agrees. "
            "Never invent goals/targets or claim notification delivery. "
            "If information is missing, ask a brief follow-up. "
            "Do not modify XP, level, streak, mastery, rank, or quest completion."
        )

        messages: list[dict] = [
            {"role": "system", "content": system_prompt}
        ]
        for turn in reversed(prior):
            messages.append({"role": turn.role, "content": turn.content})
        messages.append({"role": "user", "content": user_message})

        # Check and compact if token limit exceeded
        messages = await self._compact_conversation_if_needed(player_id, messages)

        tools = self._conversation_tool_specs()
        import re
        allow_confirmation = bool(re.search(
            r"\b(yes|yeah|yep|confirm|confirmed|agree|approved|looks good|that's right|that is right|proceed)\b",
            user_message.lower(),
        ))

        for _ in range(4):
            assistant_message = await asyncio.to_thread(
                ai_client.chat_completion_with_tools, messages, tools
            )
            if assistant_message is None:
                reply = await asyncio.to_thread(
                    ai_client.chat,
                    player_context=context,
                    user_message=user_message,
                )
                reply = reply or "The System is temporarily unavailable. Your progress remains intact."
                break

            tool_calls = assistant_message.get("tool_calls") or []
            if not tool_calls:
                reply = assistant_message.get("content") or "The System has no response at this time."
                break

            messages.append(assistant_message)
            for call in tool_calls:
                function = call.get("function", {})
                tool_name = function.get("name", "")
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                    if not isinstance(arguments, dict):
                        raise ValueError("Tool arguments must be an object.")
                    result = await self._execute_conversation_tool(
                        player_id,
                        tool_name,
                        arguments,
                        allow_confirmation=allow_confirmation,
                        had_pending_profile_before_turn=had_pending_profile,
                    )
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    result = {"ok": False, "error": f"Invalid tool arguments: {exc}"}
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": json.dumps(result, ensure_ascii=False),
                })
        else:
            reply = "I could not complete that request in one interaction. Please clarify what you want me to do."

        # Save conversation turns and extract memories
        with SessionLocal() as db:
            self.memory.add_turn(player_id, "user", user_message)
            self.memory.add_turn(player_id, "assistant", reply)
            # Extract long-term memories from this turn
            await self.memory.extract_memories(player_id, user_message, reply)
            # Check if summarization is needed
            self.memory.maybe_summarize(player_id)
            db.commit()

        return reply
    
    def close(self):
        if self._owns_session and self.session:
            self.session.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
    
    def __aenter__(self):
        return self
    
    def __aexit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
    
    def _get_notifications(self):
        if self.notifications is None:
            self.notifications = get_notification_manager(self.session, bot=self.bot)
        return self.notifications

    # ==================== Recovery Agent ====================
    
    async def diagnose_job_failures(self, player_id: int, days_back: int = 7) -> list[dict]:
        """Diagnose recent job failures for a player."""
        from kaizen.db.models import AgentJobRun
        
        since = datetime.now(timezone.utc) - timedelta(days=days_back)
        
        with SessionLocal() as db:
            runs = (
                db.query(AgentJobRun)
                .filter(
                    AgentJobRun.player_id == player_id,
                    AgentJobRun.started_at >= since,
                    AgentJobRun.success == False,
                )
                .order_by(AgentJobRun.started_at.desc())
                .all()
            )
            
            results = []
            for run in runs:
                results.append({
                    "job_name": run.job_name,
                    "started_at": run.started_at.isoformat(),
                    "error": run.error_message,
                    "fallback_used": run.fallback_used,
                })
            return results
    
    async def repair_today_quests(self, player_id: int) -> dict:
        """Attempt to repair today's quests for a player."""
        from kaizen.core.quests import get_today_quests, ensure_daily_quests
        from kaizen.db.models import Skill, Quest
        
        with SessionLocal() as db:
            player = db.query(Player).filter(Player.id == player_id).first()
            if not player or not player.active_skill:
                return {"success": False, "error": "No active player or skill"}
            
            skill = player.active_skill
            player_id = player.id
            skill_name = skill.name
            player_level = player.level
            player_rank = player.rank
            # Fetch skill profile for goal alignment
            profile = db.query(SkillProfile).filter_by(skill_id=skill.id).first()
            goal = profile.goal if profile else None
            target = profile.target if profile else None
            constraints = profile.constraints if profile else None
            
            # Store before state
            before = {
                "xp": player.current_xp,
                "level": player.level,
                "streak": player.current_streak,
                "days_cleared": player.total_days_cleared,
                "mastery": player.active_skill.mastery_pct,
            }
            
            try:
                # Remove today's quests
                today_quests = get_today_quests(db, player.id)
                for q in today_quests:
                    db.delete(q)
                db.flush()
                
                # Generate new quests (run in thread pool to avoid blocking)
                ai_quests = await asyncio.to_thread(
                    ai_client.generate_daily_quests,
                    skill_name=skill.name,
                    player_level=player.level,
                    player_rank=player.rank,
                    goal=goal,
                    target=target,
                    constraints=constraints,
                )
                new_quests = ensure_daily_quests(db, player, quests_data=ai_quests)
                
                after = {
                    "xp": player.current_xp,
                    "level": player.level,
                    "streak": player.current_streak,
                    "days_cleared": player.total_days_cleared,
                    "mastery": skill.mastery_pct,
                }
                
                # Verify no progress changed
                if before != after:
                    db.rollback()
                    logger.error(f"Quest recovery modified player progress for player {player_id}")
                    return {"success": False, "error": "Progress changed during recovery, rolled back"}
                
                db.commit()
                
                # Log recovery
                audit = RecoveryAudit(
                    player_id=player_id,
                    skill_id=skill.id,
                    action="regenerated",
                    old_quests=json.dumps([{"title": q.title, "difficulty": q.difficulty} 
                                            for q in get_today_quests(SessionLocal(), player_id)]),
                    new_quests=json.dumps([{"title": q.title, "difficulty": q.difficulty} 
                                            for q in get_today_quests(db, player_id)]),
                    reason="Manual repair via /agent repair-quests",
                    performed_at=datetime.utcnow(),
                )
                db.add(audit)
                db.commit()
                
                return {
                    "success": True,
                    "message": f"Regenerated {len(new_quests)} quests for {skill.name}",
                    "quests": [{"title": q.title, "difficulty": q.difficulty, "xp": q.xp_reward} 
                              for q in new_quests],
                }
                
            except Exception as e:
                db.rollback()
                logger.error(f"Quest repair failed for player {player_id}: {e}")
                return {"success": False, "error": str(e)}
    
    async def diagnose_and_repair(self, player_id: int) -> dict:
        """Full diagnostic and repair workflow."""
        # Check recent failures
        failures = await self.diagnose_job_failures(player_id)
        
        # Try to repair if there are failures
        if any(f["job_name"] == "daily_reset_job" for f in failures):
            result = await self.repair_today_quests(player_id)
            return {
                "failures": failures,
                "repair_result": result,
            }
        
        return {
            "failures": failures,
            "repair_result": {"success": True, "message": "No quest failures to repair"},
        }

    # ==================== Notification Agent ====================
    
    async def generate_daily_notifications(self, player_id: int) -> int:
        """Generate and queue notifications for a player's active skills."""
        notif_mgr = self._get_notifications()
        return await notif_mgr.schedule_daily_notifications(player_id)
    
    async def process_notification_outbox(self) -> int:
        """Process pending notifications in the outbox."""
        return await self._get_notifications().process_outbox()
    
    # ==================== Scheduled Jobs ====================
    
    async def run_daily_reset(self) -> dict:
        """Run the daily quest reset job for all players."""
        logger.info("Running daily reset job: generating new quests for all players.")
        results = {"processed": 0, "fallback": 0, "errors": 0}
        
        with SessionLocal() as db:
            players = db.query(Player).all()
            for player in players:
                if not player.active_skill:
                    continue
                try:
                    skill = player.active_skill
                    skill_name = skill.name
                    # Load profile for goal alignment
                    from kaizen.db.models import SkillProfile
                    profile = db.query(SkillProfile).filter_by(skill_id=skill.id).first()
                    goal = profile.goal if profile else None
                    target = profile.target if profile else None
                    constraints = profile.constraints if profile else None
                    # Run synchronous AI call in thread pool to avoid blocking event loop
                    ai_quests = await asyncio.to_thread(
                        ai_client.generate_daily_quests,
                        skill_name=skill_name,
                        player_level=player.level,
                        player_rank=player.rank,
                        goal=goal,
                        target=target,
                        constraints=constraints,
                    )
                    # Remove old quests for today
                    db.query(Quest).filter(
                        Quest.player_id == player.id,
                        Quest.quest_date == datetime.now(timezone.utc).date(),
                    ).delete(synchronize_session=False)
                    
                    # Ensure new quests
                    from kaizen.core.quests import ensure_daily_quests
                    ensure_daily_quests(db, player, quests_data=ai_quests)
                    
                    # Log job run
                    job_run = AgentJobRun(
                        job_name="daily_reset_job",
                        player_id=player.id,
                        skill_id=player.active_skill.id if player.active_skill else None,
                        started_at=datetime.now(timezone.utc),
                        success=True,
                        fallback_used=ai_quests is None,
                    )
                    db.add(job_run)
                    
                    results["processed"] += 1
                    if ai_quests is None:
                        results["fallback"] += 1
                except Exception as e:
                    logger.error(f"Daily reset failed for player {player.id}: {e}")
                    results["errors"] += 1
                    # Log failure
                    job_run = AgentJobRun(
                        job_name="daily_reset_job",
                        player_id=player.id,
                        skill_id=player.active_skill.id if player.active_skill else None,
                        started_at=datetime.now(timezone.utc),
                        completed_at=datetime.now(timezone.utc),
                        success=False,
                        fallback_used=True,
                        error_message=str(e),
                    )
                    db.add(job_run)
            db.commit()
        
        logger.info(f"Daily reset job completed: {results}")
        return results
    
    async def run_penalty_check(self) -> dict:
        """Run penalty check job."""
        from kaizen.core.progress import check_and_apply_penalty
        
        logger.info("Running penalty check job.")
        results = {"checked": 0, "penalties": 0}
        
        with SessionLocal() as db:
            players = db.query(Player).all()
            for player in players:
                penalty_res = check_and_apply_penalty(db, player)
                if penalty_res.penalty_incurred:
                    logger.info(
                        "Penalty applied for player %s: %s",
                        player.discord_user_id,
                        penalty_res.message,
                    )
                    results["penalties"] += 1
                results["checked"] += 1
            db.commit()
        return results
    
    async def run_reminder_job(self) -> dict:
        """Run reminder notification job."""
        logger.info("Running reminder job: generating daily notifications.")
        results = {"notifications_sent": 0, "errors": 0}
        
        with SessionLocal() as db:
            players = db.query(Player).all()
            for player in players:
                try:
                    # Check if notifications are enabled
                    prefs = db.query(NotificationPreference).filter(
                        NotificationPreference.player_id == player.id
                    ).first()
                    if not prefs or not prefs.notifications_enabled or prefs.paused:
                        continue
                    
                    # Generate and send notifications
                    await self.generate_daily_notifications(player.id)
                    sent = await self.process_notification_outbox()
                    results["notifications_sent"] += sent
                except Exception as e:
                    logger.error(f"Reminder job failed for player {player.id}: {e}")
                    results["errors"] += 1
        
        return results
    
    # ==================== User-facing Commands ====================
    
    async def handle_agent_status(self, player_id: int) -> dict:
        """Get agent status for a player."""
        with SessionLocal() as db:
            # Get latest job run
            latest_job = (
                self.session.query(AgentJobRun)
                .filter(AgentJobRun.player_id == player_id)
                .order_by(AgentJobRun.started_at.desc())
                .first()
            )
            
            # Get recent failures
            failures = await self.diagnose_job_failures(player_id, days_back=3)
            
            # Check if using fallback quests today
            from kaizen.core.quests import get_today_quests
            with SessionLocal() as db:
                player = db.query(Player).filter(Player.id == player_id).first()
                today_quests = get_today_quests(db, player.id) if player else []
                using_fallback = any(q.source == "system_template" for q in today_quests) if today_quests else False
            
            return {
                "latest_job": {
                    "name": latest_job.job_name if latest_job else None,
                    "success": latest_job.success if latest_job else None,
                    "started_at": latest_job.started_at.isoformat() if latest_job else None,
                    "fallback_used": latest_job.fallback_used if latest_job else False,
                },
                "recent_failures": failures,
                "using_fallback_quests": using_fallback,
            }
    
    async def handle_agent_diagnose(self, player_id: int) -> dict:
        """Get detailed diagnosis of recent issues."""
        failures = await self.diagnose_job_failures(player_id, days_back=14)
        
        if not failures:
            return {"status": "healthy", "message": "No recent failures detected."}
        
        # Analyze patterns
        error_types = {}
        for f in failures:
            key = f["error"][:50] if f["error"] else "unknown"
            error_types[key] = error_types.get(key, 0) + 1
        
        # Determine primary issue
        primary_issue = max(error_types.items(), key=lambda x: x[1])[0] if error_types else "unknown"
        
        return {
            "status": "issues_detected",
            "failure_count": len(failures),
            "primary_issue": primary_issue,
            "error_types": error_types,
            "recent_failures": failures[:5],
        }
    
    async def handle_agent_repair(self, player_id: int) -> dict:
        """Trigger quest repair for a player."""
        return await self.repair_today_quests(player_id)


def get_agent_manager(session: Session | None = None, bot=None) -> AgentManager:
    """Get an agent manager instance."""
    return AgentManager(session, bot=bot)
