import asyncio
import json
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kaizen.core.player import get_or_create_player
from kaizen.db.models import Base, NotificationPreference, Player, SkillProfile


class FakeChannel:
    id = 700

    def __init__(self):
        self.sent = []

    async def send(self, content, **kwargs):
        self.sent.append((content, kwargs))


class FakeBot:
    user = SimpleNamespace(id=99)

    async def process_commands(self, message):
        raise AssertionError("Bot-authored messages must not run command processing")


def _message(content, *, raw_mentions=(), bot_author=True):
    return SimpleNamespace(
        author=SimpleNamespace(id=501, bot=bot_author),
        guild=SimpleNamespace(id=1),
        mentions=[],
        raw_mentions=list(raw_mentions),
        content=content,
        channel=FakeChannel(),
        id=900,
    )


def test_mentioned_other_bot_receives_reply_without_player_registration(monkeypatch):
    import kaizen.bot.bot as bot_module

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(bot_module, "SessionLocal", sessions)
    message = _message("<@99> hello", raw_mentions=[99])

    asyncio.run(bot_module.KaizenBot.on_message(FakeBot(), message))

    assert len(message.channel.sent) == 1
    assert "Unregistered entity" in message.channel.sent[0][0]
    assert message.channel.sent[0][1]["mention_author"] is False
    engine.dispose()


def test_unmentioned_bot_message_is_ignored(monkeypatch):
    import kaizen.bot.bot as bot_module

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        player, _ = get_or_create_player(db, discord_user_id="501", hunter_name="TestBot")
        player_id = player.id
        db.commit()
    message = _message("ordinary bot message")
    asyncio.run(bot_module.KaizenBot.on_message(FakeBot(), message))
    # Unmentioned bot message should produce NO reply
    assert message.channel.sent == []
    engine.dispose()


def test_gateway_trace_accepts_raw_string_payloads():
    import kaizen.bot.bot as bot_module

    payload = json.dumps({
        "t": "MESSAGE_CREATE",
        "d": {
            "author": {"id": "501", "bot": True},
            "channel_id": "700",
            "id": "900",
            "content": "<@99> hello",
            "mentions": [{"id": "99"}],
        },
    })
    asyncio.run(bot_module.KaizenBot.on_socket_raw_receive(FakeBot(), payload))
    asyncio.run(bot_module.KaizenBot.on_socket_raw_receive(FakeBot(), payload.encode("utf-8")))
    asyncio.run(bot_module.KaizenBot.on_socket_raw_receive(FakeBot(), "not-json"))
