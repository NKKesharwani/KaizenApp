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
