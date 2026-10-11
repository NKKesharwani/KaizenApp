import json

from kaizen.ai.client import AIClient


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self, models_response: FakeResponse, completion_response: FakeResponse) -> None:
        self.models_response = models_response
        self.completion_response = completion_response
        self.get_calls: list[dict] = []
        self.post_calls: list[dict] = []

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.get_calls.append({"url": url, **kwargs})
        return self.models_response

    def post(self, url: str, **kwargs) -> FakeResponse:
        self.post_calls.append({"url": url, **kwargs})
        return self.completion_response


def make_client(models: FakeResponse, completion: FakeResponse) -> tuple[AIClient, FakeSession]:
    client = AIClient()
    session = FakeSession(models, completion)
    client._client = session
    client._api_key = "test-key"
    client._base_url = "http://localhost:3001/v1"
    client._configured_model = "stale-model"
    client._models_checked = False
    client._resolved_model = None
    return client, session


def test_discovers_local_model_and_sends_openai_compatible_chat_request() -> None:
    client, session = make_client(
        FakeResponse(200, {"data": [{"id": "local-model-7b"}]}),
        FakeResponse(
            200,
            {"choices": [{"message": {"content": "System response"}}]},
        ),
    )

    assert client.chat("Level 2 hunter", "How do I improve?") == "System response"
    assert session.get_calls[0]["url"] == "http://localhost:3001/v1/models"
    call = session.post_calls[0]
    assert call["url"] == "http://localhost:3001/v1/chat/completions"
    assert call["json"]["model"] == "local-model-7b"
    assert all(message is not None for message in call["json"]["messages"])
    assert call["headers"]["Authorization"] == "Bearer test-key"


def test_invalid_local_api_key_does_not_send_completion_request() -> None:
    client, session = make_client(
        FakeResponse(401, {"error": {"message": "Invalid API key"}}),
        FakeResponse(200),
    )

    assert client.chat("context", "question") is None
    assert session.post_calls == []


def test_completion_route_404_returns_fallback() -> None:
    client, session = make_client(
        FakeResponse(200, {"data": [{"id": "local-model"}]}),
        FakeResponse(404, text="route not found"),
    )

    assert client.chat("context", "question") is None
    assert session.post_calls[0]["url"].endswith("/chat/completions")


def test_quest_json_response_is_parsed() -> None:
    quests = [
        {
            "title": "Practice",
            "description": "Practice for 20 minutes",
            "difficulty": "E",
            "xp_reward": 40,
        }
    ]
    client, _ = make_client(
        FakeResponse(200, {"data": [{"id": "local-model"}]}),
        FakeResponse(200, {"choices": [{"message": {"content": json.dumps(quests)}}]}),
    )

    generated = client.generate_daily_quests("Python", 1, "E")
    assert generated is not None
    assert generated[0]["source"] == "ai"
    assert generated[0]["order_index"] == 1
