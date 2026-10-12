from datetime import datetime, date, timezone
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Player(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True, autoincrement=True)
    discord_user_id = Column(String(64), unique=True, index=True, nullable=False)
    hunter_name = Column(String(64), default="Hunter", nullable=False)
    level = Column(Integer, default=1, nullable=False)
    current_xp = Column(Integer, default=0, nullable=False)
    rank = Column(String(16), default="E", nullable=False)
    current_streak = Column(Integer, default=0, nullable=False)
    max_streak = Column(Integer, default=0, nullable=False)
    total_quests_completed = Column(Integer, default=0, nullable=False)
    total_days_cleared = Column(Integer, default=0, nullable=False)
    penalty_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    skills = relationship("Skill", back_populates="player", cascade="all, delete-orphan")
    quests = relationship("Quest", back_populates="player", cascade="all, delete-orphan")
    daily_logs = relationship("DailyLog", back_populates="player", cascade="all, delete-orphan")

    @property
    def active_skill(self):
        for s in self.skills:
            if s.is_active:
                return s
        return None


class Skill(Base):
    __tablename__ = "skills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    mastery_pct = Column(Float, default=0.0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    player = relationship("Player", back_populates="skills")


class Quest(Base):
    __tablename__ = "quests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    quest_date = Column(Date, default=date.today, nullable=False, index=True)
    title = Column(String(256), nullable=False)
    description = Column(Text, nullable=False)
    difficulty = Column(String(8), default="E", nullable=False)
    xp_reward = Column(Integer, default=50, nullable=False)
    is_completed = Column(Boolean, default=False, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    source = Column(String(32), default="system", nullable=False)
    order_index = Column(Integer, default=0, nullable=False)

    player = relationship("Player", back_populates="quests")


class DailyLog(Base):
    __tablename__ = "daily_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    log_date = Column(Date, default=date.today, nullable=False, index=True)
    user_notes = Column(Text, nullable=True)
    ai_feedback = Column(Text, nullable=True)
    quests_total = Column(Integer, default=0, nullable=False)
    quests_completed = Column(Integer, default=0, nullable=False)
    day_cleared = Column(Boolean, default=False, nullable=False)
    xp_earned = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    player = relationship("Player", back_populates="daily_logs")


class ConversationHistory(Base):
    """Stores all AI interactions for context management."""
    __tablename__ = "conversation_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    timestamp = Column(DateTime, default=utc_now, nullable=False)
    role = Column(String(16), nullable=False)  # "user" or "assistant"
    content = Column(Text, nullable=False)
    context_tokens = Column(Integer, default=0)
    
    player = relationship("Player")


class LongTermMemory(Base):
    """Extracted user facts, preferences, and patterns."""
    __tablename__ = "long_term_memory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    category = Column(String(64), nullable=False)  # "preference", "pattern", "trigger", "goal"
    key = Column(String(128), nullable=False)
    value = Column(Text, nullable=False)
    confidence = Column(Float, default=1.0)
    source = Column(String(64), nullable=True)  # "ai_extraction", "user_input", "system"
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)
    
    player = relationship("Player")


class NotificationLog(Base):
    """Tracks sent notifications for analytics."""
    __tablename__ = "notification_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    skill_id = Column(Integer, ForeignKey("skills.id"), nullable=True, index=True)
    sent_at = Column(DateTime, default=utc_now, nullable=False)
    notification_type = Column(String(32), nullable=False)  # "reminder", "motivation", "checkin"
    message = Column(Text, nullable=False)
    delivery_channel = Column(String(16), nullable=False)  # "discord", "dashboard"
    
    player = relationship("Player")
    skill = relationship("Skill")


class NotificationPreference(Base):
    """User notification preferences."""
    __tablename__ = "notification_preferences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, unique=True, index=True)
    timezone = Column(String(64), default="UTC")
    permitted_start_hour = Column(Integer, default=7)  # 0-23
    permitted_end_hour = Column(Integer, default=22)  # 0-23
    daily_cap = Column(Integer, default=3)
    notifications_enabled = Column(Boolean, default=True)
    delivery_channels = Column(String(32), default="discord")  # "discord", "dashboard", "both"
    paused = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)
    
    player = relationship("Player")


class SkillProfile(Base):
    """Skill-specific configuration for notifications and goals."""
    __tablename__ = "skill_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    skill_id = Column(Integer, ForeignKey("skills.id"), nullable=False, unique=True, index=True)
    goal = Column(Text, nullable=True)
    target = Column(String(256), nullable=True)
    constraints = Column(Text, nullable=True)
    confirmed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)
    
    skill = relationship("Skill")


class NotificationOutbox(Base):
    """Persistent outbox for scheduled notifications."""
    __tablename__ = "notification_outbox"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    skill_id = Column(Integer, ForeignKey("skills.id"), nullable=True, index=True)
    scheduled_at = Column(DateTime, nullable=False, index=True)
    message = Column(Text, nullable=False)
    notification_type = Column(String(32), nullable=False)
    status = Column(String(16), default="pending")  # "pending", "sent", "failed", "cancelled"
    attempts = Column(Integer, default=0)
    max_attempts = Column(Integer, default=3)
    idempotency_key = Column(String(128), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    sent_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    
    player = relationship("Player")
    skill = relationship("Skill")


class AgentJobRun(Base):
    """Records scheduled job results and agent actions."""
    __tablename__ = "agent_job_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_name = Column(String(64), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=True, index=True)
    skill_id = Column(Integer, ForeignKey("skills.id"), nullable=True, index=True)
    started_at = Column(DateTime, default=utc_now, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    success = Column(Boolean, nullable=True)
    fallback_used = Column(Boolean, default=False)
    error_message = Column(Text, nullable=True)
    details = Column(Text, nullable=True)
    
    player = relationship("Player")
    skill = relationship("Skill")


class ConversationSummary(Base):
    """Rolling summaries of conversation history."""
    __tablename__ = "conversation_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    summary = Column(Text, nullable=False)
    token_count = Column(Integer, default=0)
    covered_from = Column(DateTime, nullable=False)
    covered_to = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    
    player = relationship("Player")


class RecoveryAudit(Base):
    """Audit trail for recovery agent quest changes."""
    __tablename__ = "recovery_audits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    skill_id = Column(Integer, ForeignKey("skills.id"), nullable=True, index=True)
    action = Column(String(32), nullable=False)  # "regenerated", "repaired", "replaced"
    old_quests = Column(Text, nullable=True)
    new_quests = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)
    ai_output = Column(Text, nullable=True)
    performed_at = Column(DateTime, default=utc_now, nullable=False)
    
    player = relationship("Player")
    skill = relationship("Skill")
