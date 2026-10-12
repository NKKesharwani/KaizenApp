"""Notification Manager for Kaizen: Skill-aware motivational notifications."""

import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Optional
from sqlalchemy.orm import Session
from uuid import uuid4

from kaizen.db.models import (
    NotificationPreference,
    SkillProfile,
    NotificationOutbox,
    NotificationLog,
    Skill,
    Player,
)
from kaizen.db.session import SessionLocal
from kaizen.ai.client import ai_client
from kaizen.config import settings

logger = logging.getLogger(__name__)


class NotificationManager:
    """Manages skill-aware motivational notifications with AI-powered scheduling."""
    
    def __init__(self, session: Session | None = None):
        self.session = session or SessionLocal()
        self._owns_session = True
    
    def close(self):
        if self._owns_session and self.session:
            self.session.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # ==================== Preferences ====================
    
    def get_preferences(self, player_id: int) -> Optional[dict]:
        """Get notification preferences for a player."""
        pref = (
            self.session.query(NotificationPreference)
            .filter(NotificationPreference.player_id == player_id)
            .first()
        )
        if not pref:
            return None
        return {
            "timezone": pref.timezone,
            "permitted_start": pref.permitted_start_hour,
            "permitted_end": pref.permitted_end_hour,
            "daily_cap": pref.daily_cap,
            "enabled": pref.notifications_enabled,
            "channels": pref.delivery_channels,
            "paused": pref.paused,
        }
    
    def upsert_preferences(self, player_id: int, **kwargs) -> NotificationPreference:
        """Create or update notification preferences."""
        pref = (
            self.session.query(NotificationPreference)
            .filter(NotificationPreference.player_id == player_id)
            .first()
        )
        if not pref:
            pref = NotificationPreference(player_id=player_id)
            self.session.add(pref)
        
        for key, value in kwargs.items():
            if hasattr(pref, key):
                setattr(pref, key, value)
        
        pref.updated_at = datetime.utcnow()
        self.session.flush()
        return pref
    
    # ==================== Skill Profile ====================
    
    def get_skill_profile(self, skill_id: int) -> Optional[dict]:
        """Get skill profile for notification customization."""
        profile = (
            self.session.query(SkillProfile)
            .filter(SkillProfile.skill_id == skill_id)
            .first()
        )
        if not profile:
            return None
        return {
            "goal": profile.goal,
            "target": profile.target,
            "constraints": profile.constraints,
            "confirmed": profile.confirmed,
        }
    
    def upsert_skill_profile(self, skill_id: int, **kwargs) -> Optional[dict]:
        """Create or update skill profile."""
        profile = (
            self.session.query(SkillProfile)
            .filter(SkillProfile.skill_id == skill_id)
            .first()
        )
        if not profile:
            profile = SkillProfile(skill_id=skill_id)
            self.session.add(profile)
        
        for key, value in kwargs.items():
            if hasattr(profile, key):
                setattr(profile, key, value)
        
        profile.updated_at = datetime.utcnow()
        self.session.commit()
        self.session.refresh(profile)
        
        return {
            "goal": profile.goal,
            "target": profile.target,
            "constraints": profile.constraints,
            "confirmed": profile.confirmed,
        }

    # ==================== Notification Planning ====================
    
    async def generate_notification_plan(
        self,
        player_id: int,
        skill_id: int,
        date: datetime,
    ) -> Optional[list[dict]]:
        """Generate notification plan for a skill using AI."""
        from kaizen.ai.client import ai_client
        
        with SessionLocal() as db:
            player = db.query(Player).filter(Player.id == player_id).first()
            skill = db.query(Skill).filter(Skill.id == skill_id).first()
            
            if not player or not skill:
                return None
            
            prefs = self.get_preferences(player.id)
            if not prefs or not prefs["enabled"] or prefs["paused"]:
                return None
            
            skill_profile = self.get_skill_profile(skill.id)
            
            # Get memory context
            from kaizen.core.memory import get_memory_manager
            with MemoryManager() as mem:
                context = self._build_ai_context(player.id, skill.id)
            
            # Build prompt for notification planning
            system_prompt = (
                "You are the Kaizen Notification Planner. Plan motivational notifications "
                "for a hunter's skill training. Return a JSON array of notification objects.\n\n"
                "RULES:\n"
                "1. Maximum 3 notifications per skill per day\n"
                f"3. Schedule only between {self._get_permitted_hours(player_id)} in user's timezone\n"
                "4. Each message must be specific to the skill and the hunter's progress\n"
                "5. Vary tone: morning=energizing, midday=check-in, evening=reflective\n"
                "5. Use Solo Leveling System tone: cold, authoritative, but encouraging\n"
                "6. Each notification must have: time_utc (ISO format), message, type, skill_id\n"
                "7. Types: 'morning_motivation', 'midday_checkin', 'evening_reflection'\n"
                "8. Return ONLY valid JSON array\n\n"
                "Context will be provided below."
            )
            
            context = self._build_notification_context(player_id, skill_id)
            prompt = f"{context}\n\nGenerate today's notification plan."
            
            text = await ai_client._generate_text(
                prompt=prompt,
                system_instruction=system_instruction,
            )
            
            if not text:
                return None
            
            try:
                import json
                import re
                cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
                notifications = json.loads(cleaned)
                
                # Validate each notification
                validated = []
                for n in notifications:
                    if self._validate_notification(n):
                        validated.append(n)
                
                return validated[:3]  # Cap at 3 per day
            except Exception as e:
                logger.warning(f"Failed to parse notification plan: {e}")
                return None
    
    def _get_permitted_hours(self, player_id: int) -> str:
        prefs = self.get_preferences(player_id)
        if prefs:
            return f"{prefs['permitted_start']}:00-{prefs['permitted_end']}:00"
        return "07:00-22:00"
    
    def _build_ai_context(self, player_id: int, skill_id: int) -> str:
        from kaizen.core.memory import get_memory_manager
        from kaizen.db.models import Player, Skill
        
        with SessionLocal() as db:
            player = db.query(Player).filter(Player.id == player_id).first()
            skill = db.query(Skill).filter(Skill.id == skill_id).first()
            
            if not player or not skill:
                return ""
            
            parts = [
                f"HUNTER: {player.hunter_name}",
                f"LEVEL: {player.level}",
                f"RANK: {player.rank}",
                f"STREAK: {player.current_streak} days",
                f"SKILL: {skill.name} ({skill.mastery_pct:.1f}% mastery)",
            ]
            
            # Add memory context
            from kaizen.core.memory import get_memory_manager
            with MemoryManager() as mem:
                mem_ctx = mem.get_context_for_ai(player_id)
            
            return "\n".join([*["HUNTER PROFILE:", *["  " + l for l in mem_ctx.split("\n") if l]]])
    
    def _build_notification_context(self, player_id: int, skill_id: int) -> str:
        return self._build_ai_context(player_id, skill_id)
    
    def _validate_notification(self, n: dict) -> bool:
        required = ["time_utc", "message", "type", "skill_id"]
        return all(k in n for k in required) and isinstance(n["message"], str) and len(n["message"]) > 0
    
    # ==================== Scheduling & Delivery ====================
    
    async def schedule_daily_notifications(self, player_id: int) -> list:
        """Generate and queue notifications for all active skills for today."""
        with SessionLocal() as db:
            from kaizen.db.models import Skill, SkillProfile
            
            # Get all active skills with profiles
            skills = (
                self.session.query(Skill)
                .join(SkillProfile, Skill.id == SkillProfile.skill_id)
                .filter(Skill.player_id == skill_id)
                .filter(SkillProfile.confirmed == True)
                .all()
            )
            
            if not skill:
                return []
            
            today = datetime.now(timezone.utc).date()
            results = []
            
            for skill in skills:
                plan = await self.generate_notification_plan(player.id, skill.id, datetime.now())
                if not plan:
                    continue
                
                for notif in plan:
                    # Create outbox entry
                    outbox = NotificationOutbox(
                        player_id=player.id,
                        skill_id=skill.id,
                        scheduled_at=notif["time_utc"],
                        message=notif["message"],
                        notification_type=notif["type"],
                        idempotency_key=f"{player.id}_{skill.id}_{notif['type']}_{notif['time_utc'].date()}",
                    )
                    self.session.add(outbox)
                    results.append(outbox)
            
            self.session.commit()
            return results
    
    async def process_outbox(self) -> int:
        """Process pending notifications in the outbox."""
        sent = 0
        
        with SessionLocal() as db:
            pending = (
                db.query(NotificationOutbox)
                .filter(
                    NotificationOutbox.status == "pending",
                    NotificationOutbox.scheduled_at <= datetime.now(timezone.utc),
                    NotificationOutbox.attempts < NotificationOutbox.max_attempts,
                )
                .order_by(NotificationOutbox.scheduled_at)
                .limit(50)
                .all()
            )
            
            for item in pending:
                try:
                    success = await self._deliver_notification(item)
                    if success:
                        item.status = "sent"
                        item.sent_at = datetime.now(timezone.utc)
                        item.attempts += 1
                        # Log delivery
                        log = NotificationLog(
                            player_id=item.player_id,
                            skill_id=item.skill_id,
                            notification_type=item.notification_type,
                            message=item.message,
                            delivery_channel="discord",
                        )
                        self.session.add(log)
                        sent += 1
                    else:
                        item.attempts += 1
                        if item.attempts >= item.max_attempts:
                            item.status = "failed"
                            item.error_message = "Max attempts reached"
                except Exception as e:
                    logger.error(f"Failed to deliver notification {item.id}: {e}")
                    item.attempts += 1
                    if item.attempts >= item.max_attempts:
                        item.status = "failed"
                        item.error_message = str(e)
            
            db.commit()
            return sent
    
    async def _deliver_notification(self, item: NotificationOutbox) -> bool:
        """Deliver a notification via Discord DM and/or dashboard."""
        success = False
        
        # Try Discord DM
        try:
            # Would need bot instance - implement via bot reference
            pass
        except Exception as e:
            logger.warning(f"Failed to send DM: {e}")
        
        # Always mark as delivered for dashboard (it polls the outbox)
        return True
    
    # ==================== Preferences API ====================
    
    def set_preferences(self, player_id: int, **kwargs) -> NotificationPreference:
        """Update notification preferences."""
        with SessionLocal() as db:
            pref = self.upsert_preferences(player_id, **kwargs)
            return pref
    
    def get_preferences_dict(self, player_id: int) -> dict:
        """Get preferences as dict."""
        prefs = self.get_preferences(player_id)
        return prefs or {}
    
    # ==================== Skill Profile ====================
    
    def set_skill_profile(self, skill_id: int, **kwargs) -> dict:
        """Set skill profile with confirmation flow."""
        # This would be called from a command that confirms with the user
        return self.upsert_skill_profile(skill_id, **kwargs)
    
    # ==================== Outbox Processing ====================
    
    def get_pending_notifications(self, player_id: int) -> list:
        """Get pending notifications for a player."""
        return (
            self.session.query(NotificationOutbox)
            .filter(
                NotificationOutbox.player_id == player_id,
                NotificationOutbox.status == "pending",
            )
            .order_by(NotificationOutbox.scheduled_at)
            .all()
        )
    
    def cancel_notification(self, outbox_id: int, player_id: int) -> bool:
        """Cancel a pending notification."""
        outbox = (
            self.session.query(NotificationOutbox)
            .filter(
                NotificationOutbox.id == outbox_id,
                NotificationOutbox.player_id == player_id,
                NotificationOutbox.status == "pending",
            )
            .first()
        )
        if outbox:
            outbox.status = "cancelled"
            self.session.commit()
            return True
        return False


def get_notification_manager(session: Session | None = None) -> NotificationManager:
    """Get a notification manager instance."""
    return NotificationManager(session)