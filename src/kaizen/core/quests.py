from dataclasses import dataclass
from datetime import date, datetime, timezone
from sqlalchemy.orm import Session

from kaizen.core.player import XpGainResult, add_xp_to_player, calculate_rank
from kaizen.db.models import DailyLog, Player, Quest, Skill


@dataclass
class QuestCompletionResult:
    quest: Quest
    xp_result: XpGainResult
    daily_cleared: bool
    new_mastery_pct: float
    streak: int
    rank_changed: bool
    old_rank: str
    new_rank: str
    bonus_xp: int = 0


def get_today_quests(db: Session, player_id: int, target_date: date | None = None) -> list[Quest]:
    """Retrieve all quests for player on given date (defaults to today)."""
    check_date = target_date or date.today()
    return (
        db.query(Quest)
        .filter(Quest.player_id == player_id, Quest.quest_date == check_date)
        .order_by(Quest.order_index.asc())
        .all()
    )


def generate_fallback_quests(skill_name: str) -> list[dict]:
    """Generate 3 Solo Leveling themed template quests for a given skill."""
    return [
        {
            "title": f"[DAILY QUEST: PHASE 1] {skill_name} Fundamentals",
            "description": f"Dedicate 25 minutes of unbroken focus to core {skill_name} fundamentals and basics.",
            "difficulty": "E",
            "xp_reward": 50,
            "order_index": 1,
        },
        {
            "title": f"[DAILY QUEST: PHASE 2] {skill_name} Deep Execution",
            "description": f"Engage in 35 minutes of hands-on application or problem solving in {skill_name}.",
            "difficulty": "D",
            "xp_reward": 75,
            "order_index": 2,
        },
        {
            "title": f"[DAILY QUEST: PHASE 3] Reflection & Battle Log",
            "description": "Document 1 key takeaway, mistake corrected, or new concept mastered today.",
            "difficulty": "E",
            "xp_reward": 50,
            "order_index": 3,
        },
    ]


def ensure_daily_quests(
    db: Session,
    player: Player,
    quests_data: list[dict] | None = None,
    target_date: date | None = None,
) -> list[Quest]:
    """Ensure today's quests exist for the player. If not, creates them."""
    check_date = target_date or date.today()
    existing = get_today_quests(db, player.id, check_date)
    if existing:
        return existing

    skill = player.active_skill
    skill_name = skill.name if skill else "General Mastery"

    if not quests_data:
        quests_data = generate_fallback_quests(skill_name)

    created_quests: list[Quest] = []
    for q in quests_data:
        quest = Quest(
            player_id=player.id,
            quest_date=check_date,
            title=q.get("title", f"Train {skill_name}"),
            description=q.get("description", "Daily training requirement."),
            difficulty=q.get("difficulty", "E"),
            xp_reward=int(q.get("xp_reward", 50)),
            source=q.get("source", "system_template"),
            order_index=int(q.get("order_index", len(created_quests) + 1)),
            is_completed=False,
        )
        db.add(quest)
        created_quests.append(quest)

    db.flush()
    return created_quests


def complete_quest(db: Session, player: Player, quest_id: int) -> QuestCompletionResult:
    """Complete a quest, grant XP, and trigger daily 1% improvement if all daily quests are finished."""
    quest = db.query(Quest).filter(Quest.id == quest_id, Quest.player_id == player.id).first()
    if not quest:
        raise ValueError(f"Quest #{quest_id} not found for player.")

    if quest.is_completed:
        raise ValueError(f"Quest #{quest_id} is already completed.")

    quest.is_completed = True
    quest.completed_at = datetime.now(timezone.utc)
    player.total_quests_completed += 1

    # Base quest XP
    xp_result = add_xp_to_player(player, quest.xp_reward)

    # Check if all quests for this day are finished
    today_quests = get_today_quests(db, player.id, quest.quest_date)
    all_completed = all(q.is_completed for q in today_quests)

    daily_cleared = False
    bonus_xp = 0
    old_rank = player.rank
    new_rank = player.rank
    rank_changed = False
    active_skill = player.active_skill
    mastery = active_skill.mastery_pct if active_skill else 0.0

    if all_completed and len(today_quests) > 0:
        daily_log = (
            db.query(DailyLog)
            .filter(DailyLog.player_id == player.id, DailyLog.log_date == quest.quest_date)
            .first()
        )
        if not daily_log or not daily_log.day_cleared:
            daily_cleared = True
            player.total_days_cleared += 1
            player.current_streak += 1
            if player.current_streak > player.max_streak:
                player.max_streak = player.current_streak

            if active_skill:
                active_skill.mastery_pct = min(100.0, round(active_skill.mastery_pct + 1.0, 2))
                mastery = active_skill.mastery_pct

            new_rank = calculate_rank(mastery)
            if new_rank != player.rank:
                player.rank = new_rank
                rank_changed = True

            bonus_xp = 100
            xp_result = add_xp_to_player(player, bonus_xp)

            if not daily_log:
                daily_log = DailyLog(
                    player_id=player.id,
                    log_date=quest.quest_date,
                    quests_total=len(today_quests),
                    quests_completed=len(today_quests),
                    day_cleared=True,
                    xp_earned=sum(q.xp_reward for q in today_quests) + bonus_xp,
                )
                db.add(daily_log)
            else:
                daily_log.quests_completed = len(today_quests)
                daily_log.day_cleared = True
                daily_log.xp_earned += quest.xp_reward + bonus_xp
        elif daily_log:
            # A skill replacement must not grant a second daily mastery reward.
            daily_log.xp_earned += quest.xp_reward

    db.flush()

    return QuestCompletionResult(
        quest=quest,
        xp_result=xp_result,
        daily_cleared=daily_cleared,
        new_mastery_pct=mastery,
        streak=player.current_streak,
        rank_changed=rank_changed,
        old_rank=old_rank,
        new_rank=new_rank,
        bonus_xp=bonus_xp,
    )
