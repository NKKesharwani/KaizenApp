from dataclasses import dataclass
from datetime import date, timedelta
from sqlalchemy.orm import Session

from kaizen.core.player import xp_required_for_level
from kaizen.core.quests import get_today_quests
from kaizen.db.models import Player, Quest


@dataclass
class PenaltyCheckResult:
    penalty_incurred: bool
    broken_streak: int
    message: str


@dataclass
class PlayerStatusView:
    hunter_name: str
    level: int
    rank: str
    current_xp: int
    xp_needed: int
    xp_progress_pct: float
    skill_name: str
    mastery_pct: float
    streak: int
    max_streak: int
    days_cleared: int
    total_quests_completed: int
    today_quests_total: int
    today_quests_completed: int
    today_all_cleared: bool


def render_progress_bar(pct: float, length: int = 14) -> str:
    """Render a visual text progress bar like [████░░░░░░] 40.0%."""
    pct = max(0.0, min(100.0, pct))
    filled_length = int(round(length * pct / 100))
    bar = "█" * filled_length + "░" * (length - filled_length)
    return f"[{bar}] {pct:.1f}%"


def check_and_apply_penalty(db: Session, player: Player, check_date: date | None = None) -> PenaltyCheckResult:
    """Check if yesterday had uncompleted quests. If so, apply Solo Leveling penalty."""
    today = check_date or date.today()
    yesterday = today - timedelta(days=1)

    # Check yesterday's quests
    yesterday_quests = (
        db.query(Quest)
        .filter(Quest.player_id == player.id, Quest.quest_date == yesterday)
        .all()
    )

    if not yesterday_quests:
        return PenaltyCheckResult(
            penalty_incurred=False,
            broken_streak=0,
            message="No active quests yesterday.",
        )

    all_done = all(q.is_completed for q in yesterday_quests)
    if not all_done and player.current_streak > 0:
        broken_streak = player.current_streak
        player.current_streak = 0
        player.penalty_count += 1
        db.flush()
        return PenaltyCheckResult(
            penalty_incurred=True,
            broken_streak=broken_streak,
            message=f"⚠️ [SYSTEM WARNING: PENALTY ZONE ACTIVATED]\nYou failed yesterday's Daily Quest! Your {broken_streak}-day streak was broken. The System demands discipline.",
        )

    return PenaltyCheckResult(
        penalty_incurred=False,
        broken_streak=0,
        message="Daily quests satisfied.",
    )


def get_player_status(db: Session, player: Player, target_date: date | None = None) -> PlayerStatusView:
    """Get complete game status view for a player."""
    today = target_date or date.today()
    today_quests = get_today_quests(db, player.id, today)

    quests_total = len(today_quests)
    quests_done = sum(1 for q in today_quests if q.is_completed)
    today_cleared = (quests_total > 0 and quests_done == quests_total)

    needed_xp = xp_required_for_level(player.level)
    xp_pct = min(100.0, (player.current_xp / needed_xp * 100.0)) if needed_xp > 0 else 0.0

    skill = player.active_skill
    skill_name = skill.name if skill else "No Active Skill"
    mastery = skill.mastery_pct if skill else 0.0

    return PlayerStatusView(
        hunter_name=player.hunter_name,
        level=player.level,
        rank=player.rank,
        current_xp=player.current_xp,
        xp_needed=needed_xp,
        xp_progress_pct=round(xp_pct, 1),
        skill_name=skill_name,
        mastery_pct=round(mastery, 1),
        streak=player.current_streak,
        max_streak=player.max_streak,
        days_cleared=player.total_days_cleared,
        total_quests_completed=player.total_quests_completed,
        today_quests_total=quests_total,
        today_quests_completed=quests_done,
        today_all_cleared=today_cleared,
    )
