from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from kaizen.db.models import Base, Player
from kaizen.db.session import get_db
from kaizen.web.app import app
from kaizen.web.routes import dashboard


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db() -> Generator[Session, None, None]:
        db = test_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.state.test_session = test_session
    monkeypatch.setattr(dashboard.ai_client, "generate_daily_quests", lambda **_: None)

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    del app.state.test_session
    engine.dispose()


def test_dashboard_uses_anime_template_and_stylesheet(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/static/css/style.css"' in response.text
    assert "System Awakening Window" in response.text
    assert "system-card" in response.text

    stylesheet = client.get("/static/css/style.css")
    assert stylesheet.status_code == 200
    assert "--bg-primary: #050813" in stylesheet.text
    assert ".system-card" in stylesheet.text


def test_registered_player_dashboard_renders_character_sheet(client: TestClient) -> None:
    response = client.post(
        "/api/register",
        data={"hunter_name": "Test Hunter", "skill_name": "Drawing"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Test Hunter" in response.text
    assert "Drawing Mastery" in response.text
    assert "Daily Quest" in response.text
    assert 'href="/static/css/style.css"' in response.text
    assert "Remove Active Skill" in response.text


def test_dashboard_can_remove_and_replace_active_skill(client: TestClient) -> None:
    client.post(
        "/api/register",
        data={"hunter_name": "Test Hunter", "skill_name": "Wrong Skill"},
    )

    removed = client.post("/api/skill/remove", follow_redirects=True)
    assert removed.status_code == 200
    assert "Choose a New Skill" in removed.text
    assert "Wrong Skill Mastery" not in removed.text

    replaced = client.post(
        "/api/register",
        data={"hunter_name": "Test Hunter", "skill_name": "Correct Skill"},
        follow_redirects=True,
    )
    assert replaced.status_code == 200
    assert "Correct Skill Mastery" in replaced.text


def test_dashboard_reactivates_displayed_discord_player(client: TestClient) -> None:
    with app.state.test_session() as db:
        from kaizen.core.player import get_or_create_player, remove_active_skill

        player, _ = get_or_create_player(
            db,
            discord_user_id="discord-player",
            hunter_name="Discord Hunter",
            initial_skill="Old Skill",
        )
        remove_active_skill(db, player)
        player_id = player.id
        db.commit()

    response = client.post(
        "/api/register",
        data={"hunter_name": "Ignored", "skill_name": "New Skill"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Discord Hunter" in response.text
    assert "New Skill Mastery" in response.text
    with app.state.test_session() as db:
        assert db.query(Player).count() == 1
        player = db.query(Player).filter(Player.id == player_id).one()
        assert player.active_skill.name == "New Skill"
