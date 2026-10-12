import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kaizen.core.agent import AgentManager
from kaizen.core.player import get_or_create_player
from kaizen.db.models import Base, NotificationPreference, Player, SkillProfile


def _test_sessions():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def test_profile_tool_requires_separate_explicit_confirmation(monkeypatch):
    import kaizen.core.agent as agent_module

    engine, sessions = _test_sessions()
    monkeypatch.setattr(agent_module, "SessionLocal", sessions)
    with sessions() as db:
        player, _ = get_or_create_player(
            db, discord_user_id="101", hunter_name="Tool Hunter", initial_skill="Python"
        )
        player_id = player.id
        skill_id = player.active_skill.id
        db.commit()

    agent = AgentManager()
    proposed = asyncio.run(agent._execute_conversation_tool(
        player_id,
        "propose_skill_profile",
        {"goal": "Build useful Python projects", "target": "Complete three projects", "constraints": "30 minutes daily"},
    ))
    assert proposed["ok"] is True
    assert proposed["confirmed"] is False

    rejected_confirmation = asyncio.run(agent._execute_conversation_tool(
        player_id,
        "confirm_skill_profile",
        {},
        allow_confirmation=False,
        had_pending_profile_before_turn=True,
    ))
    assert rejected_confirmation["ok"] is False

    confirmed = asyncio.run(agent._execute_conversation_tool(
        player_id,
        "confirm_skill_profile",
        {},
        allow_confirmation=True,
        had_pending_profile_before_turn=True,
    ))
    assert confirmed["ok"] is True
    with sessions() as db:
        profile = db.query(SkillProfile).filter_by(skill_id=skill_id).one()
        assert profile.confirmed is True
        assert profile.target == "Complete three projects"
    agent.close()
    engine.dispose()


def test_conversation_calls_registered_tools_and_persists_turns(monkeypatch):
    import kaizen.core.agent as agent_module

    engine, sessions = _test_sessions()
    monkeypatch.setattr(agent_module, "SessionLocal", sessions)
    with sessions() as db:
        player, _ = get_or_create_player(
            db, discord_user_id="202", hunter_name="Chat Hunter", initial_skill="Drawing"
        )
        player_id = player.id
        db.commit()

    tool_call = {
        "id": "call_profile",
        "type": "function",
        "function": {
            "name": "propose_skill_profile",
            "arguments": json.dumps({
                "goal": "Draw portraits",
                "target": "Finish five portraits",
                "constraints": "Practice after work",
            }),
        },
    }
    responses = iter([
        {"role": "assistant", "tool_calls": [tool_call]},
        {"role": "assistant", "content": "I saved that as a proposal. Review it and tell me if you confirm."},
    ])
    monkeypatch.setattr(agent_module.ai_client, "chat_completion_with_tools", lambda *_args: next(responses))

    agent = AgentManager()
    reply = asyncio.run(agent.handle_conversational_request(
        player_id, "My goal is drawing portraits, finish five, and practice after work."
    ))
    assert "proposal" in reply
    with sessions() as db:
        player = db.query(Player).filter_by(id=player_id).one()
        profile = db.query(SkillProfile).filter_by(skill_id=player.active_skill.id).one()
        assert profile.goal == "Draw portraits"
        assert profile.confirmed is False
        assert db.query(NotificationPreference).filter_by(player_id=player_id).count() == 1
        assert db.query(agent_module.ConversationHistory).filter_by(player_id=player_id).count() == 2
    agent.close()
    engine.dispose()


def test_notification_preference_tool_validates_timezone_and_updates_settings(monkeypatch):
    import kaizen.core.agent as agent_module

    engine, sessions = _test_sessions()
    monkeypatch.setattr(agent_module, "SessionLocal", sessions)
    with sessions() as db:
        player, _ = get_or_create_player(db, discord_user_id="303", hunter_name="Settings Hunter")
        player_id = player.id
        db.commit()

    agent = AgentManager()
    invalid = asyncio.run(agent._execute_conversation_tool(
        player_id, "set_notification_preferences", {"timezone": "Not/AZone"}
    ))
    assert invalid["ok"] is False

    result = asyncio.run(agent._execute_conversation_tool(
        player_id,
        "set_notification_preferences",
        {"notifications_enabled": True, "timezone": "Asia/Kolkata", "permitted_start_hour": 8},
    ))
    assert result["ok"] is True
    with sessions() as db:
        pref = db.query(NotificationPreference).filter_by(player_id=player_id).one()
        assert pref.notifications_enabled is True
        assert pref.timezone == "Asia/Kolkata"
        assert pref.permitted_start_hour == 8
    agent.close()
    engine.dispose()
