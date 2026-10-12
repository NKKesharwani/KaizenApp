import pytest
import asyncio
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from kaizen.core.player import add_active_skill, get_or_create_player, remove_active_skill
from kaizen.core.quests import get_today_quests, ensure_daily_quests
from kaizen.db.models import Base, NotificationPreference, Player, SkillProfile


class FakeResponse:
    async def defer(self, **kwargs) -> None:
        pass


class FakeFollowup:
    def __init__(self) -> None:
        self.sent = []

    async def send(self, *args, **kwargs) -> None:
        self.sent.append((args, kwargs))


class FakeInteraction:
    def __init__(self) -> None:
        self.user = type("User", (), {"id": 445566, "display_name": "SoloPlayer"})()
        self.response = FakeResponse()
        self.followup = FakeFollowup()


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    yield session
    session.close()


def test_register_after_remove_skill_assigns_new_skill_and_quests(db_session: Session):
    # Step 1: Create player with initial skill
    player, created = get_or_create_player(
        db_session,
        discord_user_id="112233",
        hunter_name="SoloPlayer",
        initial_skill="Python",
    )
    assert created
    assert player.active_skill.name == "Python"

    # Step 2: Remove the active skill
    removed_name = remove_active_skill(db_session, player)
    assert removed_name == "Python"
    assert player.active_skill is None
    assert get_today_quests(db_session, player.id) == []

    # Existing-player lookup must not add a skill as a side effect.
    p2, created2 = get_or_create_player(
        db_session,
        discord_user_id="112233",
        hunter_name="SoloPlayer",
        initial_skill="Boxing",
    )
    assert not created2
    assert p2.active_skill is None

    p2, created2 = get_or_create_player(
        db_session,
        discord_user_id="112233",
        hunter_name="SoloPlayer",
        initial_skill="Boxing",
    )
    assert not created2
    assert p2.active_skill is None
    add_active_skill(db_session, p2, "Boxing")
    assert p2.active_skill.name == "Boxing"

    # Ensure quests generated for new skill
    quests = ensure_daily_quests(db_session, p2)
    assert len(quests) == 3
    assert all(not q.is_completed for q in quests)


def test_discord_register_after_remove_commits_replacement_and_quests(monkeypatch):
    from kaizen.bot import bot as bot_module

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, expire_on_commit=False)

    with test_sessions() as db:
        player, _ = get_or_create_player(
            db,
            discord_user_id="445566",
            hunter_name="SoloPlayer",
            initial_skill="Old Skill",
        )
        remove_active_skill(db, player)
        db.commit()

    monkeypatch.setattr(bot_module, "SessionLocal", test_sessions)
    monkeypatch.setattr(bot_module.ai_client, "generate_daily_quests", lambda **_: None)

    bot = bot_module.KaizenBot()
    asyncio.run(bot._register_commands())
    registered_names = [command.name for command in bot.tree.get_commands()]
    assert len(registered_names) == len(set(registered_names))
    assert {"coach", "notification-setup", "notification-status"}.issubset(registered_names)
    register = bot.tree.get_command("register")
    interaction = FakeInteraction()
    asyncio.run(register.callback(interaction, "New Skill"))

    with test_sessions() as db:
        player = db.query(Player).filter_by(discord_user_id="445566").one()
        assert player.active_skill.name == "New Skill"
        assert len(get_today_quests(db, player.id)) == 3
        # Registration enables notifications but must not invent and confirm the
        # player's goal/target. Those are collected conversationally by the agent.
        preference = db.query(NotificationPreference).filter_by(player_id=player.id).one()
        assert preference.notifications_enabled is True
        assert db.query(SkillProfile).filter_by(skill_id=player.active_skill.id).count() == 0

    assert len(interaction.followup.sent) >= 1
    assert "New Skill" in interaction.followup.sent[0][1]["embed"].description
    engine.dispose()


def test_reset_quests_command_is_unique_and_preserves_player_progress(monkeypatch):
    from kaizen.bot import bot as bot_module

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, expire_on_commit=False)

    with test_sessions() as db:
        player, _ = get_or_create_player(
            db,
            discord_user_id="778899",
            hunter_name="Recovery Hunter",
            initial_skill="Wake up early",
        )
        player.level = 5
        player.current_xp = 61
        player.current_streak = 4
        player.total_days_cleared = 3
        player.active_skill.mastery_pct = 12.0
        ensure_daily_quests(db, player)
        db.commit()

    monkeypatch.setattr(bot_module, "SessionLocal", test_sessions)
    monkeypatch.setattr(bot_module.ai_client, "generate_daily_quests", lambda **_: None)

    bot = bot_module.KaizenBot()
    asyncio.run(bot._register_commands())
    commands = bot.tree.get_commands()
    names = [command.name for command in commands]
    assert len(names) == len(set(names))
    assert "reset_quests" in names
    assert names.count("remove-skill") == 1

    interaction = FakeInteraction()
    interaction.user.id = 778899
    reset_command = bot.tree.get_command("reset_quests")
    asyncio.run(reset_command.callback(interaction))

    with test_sessions() as db:
        player = db.query(Player).filter_by(discord_user_id="778899").one()
        assert (player.current_xp, player.level, player.current_streak, player.total_days_cleared) == (
            61,
            5,
            4,
            3,
        )
        assert player.active_skill.mastery_pct == 12.0
        quests = get_today_quests(db, player.id)
        assert len(quests) == 3
        assert all(quest.source == "system_template" for quest in quests)

    assert any("Recovered today's quests" in args[0] for args, _ in interaction.followup.sent)
    engine.dispose()
