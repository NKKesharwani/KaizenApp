from dataclasses import dataclass
from datetime import date
from sqlalchemy.orm import Session

from kaizen.db.models import Player, Quest, Skill


@dataclass
class XpGainResult:
    previous_level: int
    new_level: int
    levels_gained: int
    current_xp: int
    xp_needed_next: int
    leveled_up: bool


def xp_required_for_level(level: int) -> int:
    """Calculate the XP required to advance from `level` to `level + 1`."""
    if level < 1:
        level = 1
    return int(100 * (1.2 ** (level - 1)))


def calculate_rank(mastery_pct: float) -> str:
    """Solo Leveling hunter ranks based on skill mastery percentage."""
    if mastery_pct >= 95.0:
        return "S"
    elif mastery_pct >= 80.0:
        return "A"
    elif mastery_pct >= 60.0:
        return "B"
    elif mastery_pct >= 40.0:
        return "C"
    elif mastery_pct >= 20.0:
        return "D"
    return "E"


def add_xp_to_player(player: Player, xp: int) -> XpGainResult:
    """Add XP to a player and handle level ups."""
    prev_level = player.level
    player.current_xp += xp

    levels_gained = 0
    while True:
        needed = xp_required_for_level(player.level)
        if player.current_xp >= needed:
            player.current_xp -= needed
            player.level += 1
            levels_gained += 1
        else:
            break

    next_needed = xp_required_for_level(player.level)
    return XpGainResult(
        previous_level=prev_level,
        new_level=player.level,
        levels_gained=levels_gained,
        current_xp=player.current_xp,
        xp_needed_next=next_needed,
        leveled_up=(levels_gained > 0),
    )


def get_or_create_player(
    db: Session,
    discord_user_id: str,
    hunter_name: str = "Hunter",
    initial_skill: str | None = None,
) -> tuple[Player, bool]:
    """Retrieve existing player by Discord ID or create a new one."""
    player = db.query(Player).filter(Player.discord_user_id == str(discord_user_id)).first()
    created = False

    if not player:
        player = Player(
            discord_user_id=str(discord_user_id),
            hunter_name=hunter_name,
            level=1,
            current_xp=0,
            rank="E",
            current_streak=0,
            max_streak=0,
        )
        db.add(player)
        db.flush()
        created = True

        if initial_skill:
            skill = Skill(
                player_id=player.id,
                name=initial_skill,
                mastery_pct=0.0,
                is_active=True,
            )
            player.skills.append(skill)
            db.flush()
    return player, created


def add_active_skill(db: Session, player: Player, skill_name: str) -> Skill:
    """Assign a skill to an existing player who currently has none."""
    skill_name = skill_name.strip()
    if not skill_name:
        raise ValueError("Skill name cannot be empty.")
    if player.active_skill is not None:
        raise ValueError("Remove the current active skill before assigning another.")

    player.rank = "E"
    skill = Skill(name=skill_name, mastery_pct=0.0, is_active=True)
    player.skills.append(skill)
    db.flush()
    return skill


def remove_active_skill(
    db: Session,
    player: Player,
    target_date: date | None = None,
    expected_skill_id: int | None = None,
) -> str:
    """Remove the player's active skill and its quests for the selected day."""
    skill = player.active_skill
    if skill is None:
        raise ValueError("You do not have an active skill to remove.")
    if expected_skill_id is not None and skill.id != expected_skill_id:
        raise ValueError("Your active skill changed. Run the remove command again to confirm.")

    skill_name = skill.name
    for quest in db.query(Quest).filter(
        Quest.player_id == player.id,
        Quest.quest_date == (target_date or date.today()),
    ):
        db.delete(quest)
    player.rank = "E"
    player.skills.remove(skill)
    db.flush()
    return skill_name
