from datetime import date, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from kaizen.core.player import (
    add_active_skill,
    add_xp_to_player,
    calculate_rank,
    get_or_create_player,
    remove_active_skill,
    xp_required_for_level,
)
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
from kaizen.db.models import Base, Player, Quest, Skill


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    yield session
    session.close()


def test_xp_curve_and_level_up():
    assert xp_required_for_level(1) == 100
    assert xp_required_for_level(2) == 120
    assert xp_required_for_level(3) == 144

    dummy_player = Player(discord_user_id="123", hunter_name="Sung", level=1, current_xp=0)
    
    # 50 XP -> no level up
    res1 = add_xp_to_player(dummy_player, 50)
    assert not res1.leveled_up
    assert res1.new_level == 1
    assert dummy_player.current_xp == 50

    # +60 XP -> total 110 XP -> levels up to 2, remaining 10 XP
    res2 = add_xp_to_player(dummy_player, 60)
    assert res2.leveled_up
    assert res2.new_level == 2
    assert res2.levels_gained == 1
    assert dummy_player.current_xp == 10
    assert res2.xp_needed_next == 120


def test_multiple_level_ups():
    dummy = Player(discord_user_id="123", hunter_name="Sung", level=1, current_xp=0)
    # L1->L2 requires 100, L2->L3 requires 120, total 220
    res = add_xp_to_player(dummy, 250)
    assert res.new_level == 3
    assert res.levels_gained == 2
    assert dummy.current_xp == 30


def test_calculate_rank():
    assert calculate_rank(0.0) == "E"
    assert calculate_rank(19.9) == "E"
    assert calculate_rank(20.0) == "D"
    assert calculate_rank(39.9) == "D"
    assert calculate_rank(40.0) == "C"
    assert calculate_rank(60.0) == "B"
    assert calculate_rank(80.0) == "A"
    assert calculate_rank(95.0) == "S"
    assert calculate_rank(100.0) == "S"


def test_progress_bar_renderer():
    bar = render_progress_bar(50.0, length=10)
    assert "50.0%" in bar
    assert "█████░░░░░" in bar


def test_player_creation_and_quests(db_session: Session):
    player, created = get_or_create_player(
        db_session,
        discord_user_id="999",
        hunter_name="Jinwoo",
        initial_skill="Python Mastery",
    )
    assert created
    assert player.hunter_name == "Jinwoo"
    assert player.active_skill is not None
    assert player.active_skill.name == "Python Mastery"
    assert player.active_skill.mastery_pct == 0.0

    # Ensure quests are created
    quests = ensure_daily_quests(db_session, player)
    assert len(quests) == 3
    assert all(not q.is_completed for q in quests)


def test_daily_quests_completion_and_one_percent_gain(db_session: Session):
    player, _ = get_or_create_player(
        db_session,
        discord_user_id="888",
        hunter_name="ShadowMonarch",
        initial_skill="Sword Mastery",
    )
    quests = ensure_daily_quests(db_session, player)
    q1, q2, q3 = quests[0], quests[1], quests[2]

    # Complete first quest: not all done yet
    res1 = complete_quest(db_session, player, q1.id)
    assert not res1.daily_cleared
    assert res1.new_mastery_pct == 0.0
    assert player.current_streak == 0

    # Complete second quest: still not all done
    res2 = complete_quest(db_session, player, q2.id)
    assert not res2.daily_cleared
    assert res2.new_mastery_pct == 0.0

    # Complete third and final quest: triggers +1% mastery and daily clear!
    res3 = complete_quest(db_session, player, q3.id)
    assert res3.daily_cleared
    assert res3.new_mastery_pct == 1.0  # +1% achieved!
    assert player.current_streak == 1
    assert player.total_days_cleared == 1
    assert res3.bonus_xp == 100

    # Player status view test
    status = get_player_status(db_session, player)
    assert status.mastery_pct == 1.0
    assert status.today_all_cleared is True
    assert status.streak == 1


def test_penalty_break_streak(db_session: Session):
    player, _ = get_or_create_player(
        db_session,
        discord_user_id="777",
        hunter_name="Cha",
        initial_skill="Running",
    )
    player.current_streak = 5
    db_session.flush()

    yesterday = date.today() - timedelta(days=1)
    # Add an incomplete quest yesterday
    incomplete_q = Quest(
        player_id=player.id,
        quest_date=yesterday,
        title="10km Run",
        description="Daily run",
        difficulty="C",
        xp_reward=100,
        is_completed=False,
    )
    db_session.add(incomplete_q)
    db_session.flush()

    penalty = check_and_apply_penalty(db_session, player)
    assert penalty.penalty_incurred is True
    assert penalty.broken_streak == 5
    assert player.current_streak == 0
    assert player.penalty_count == 1


def test_remove_skill_keeps_player_progress_and_allows_replacement(db_session: Session):
    player, _ = get_or_create_player(
        db_session,
        discord_user_id="skill-removal",
        hunter_name="Hunter",
        initial_skill="Mistaken Skill",
    )
    player.level = 4
    player.current_xp = 37
    player.current_streak = 6
    player.max_streak = 9
    ensure_daily_quests(db_session, player)
    yesterday_quest = Quest(
        player_id=player.id,
        quest_date=date.today() - timedelta(days=1),
        title="Past quest",
        description="Keep historical quest data",
    )
    db_session.add(yesterday_quest)
    db_session.flush()

    removed_name = remove_active_skill(db_session, player)
    assert removed_name == "Mistaken Skill"
    assert player.active_skill is None
    assert player.rank == "E"
    assert player.level == 4
    assert player.current_xp == 37
    assert player.current_streak == 6
    assert player.max_streak == 9
    assert get_today_quests(db_session, player.id) == []
    assert db_session.query(Quest).filter(Quest.id == yesterday_quest.id).first() is not None

    same_player, created = get_or_create_player(
        db_session,
        discord_user_id="skill-removal",
        initial_skill="Replacement Skill",
    )
    assert not created
    assert same_player.id == player.id
    assert same_player.active_skill is None
    add_active_skill(db_session, same_player, "Replacement Skill")
    assert same_player.active_skill.name == "Replacement Skill"
    assert same_player.active_skill.mastery_pct == 0.0
    assert len(ensure_daily_quests(db_session, same_player)) == 3


def test_replacement_skill_cannot_clear_twice_in_one_day(db_session: Session):
    player, _ = get_or_create_player(
        db_session,
        discord_user_id="one-clear-per-day",
        initial_skill="First Skill",
    )
    for quest in ensure_daily_quests(db_session, player):
        complete_quest(db_session, player, quest.id)
    assert player.active_skill.mastery_pct == 1.0
    assert player.total_days_cleared == 1

    remove_active_skill(db_session, player)
    get_or_create_player(db_session, "one-clear-per-day", initial_skill="Second Skill")
    add_active_skill(db_session, player, "Second Skill")
    replacement_quests = ensure_daily_quests(db_session, player)
    results = [complete_quest(db_session, player, q.id) for q in replacement_quests]

    assert not any(result.daily_cleared for result in results)
    assert player.active_skill.mastery_pct == 0.0
    assert player.total_days_cleared == 1
