from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock
from uuid import uuid4
import httpx
import pytest
from app import llm
from app.db import Actor


def test_gigachat_uses_oauth_and_real_completion_contract(monkeypatch):
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "test-key")
    calls = []

    def respond(request):
        calls.append(request)
        if "oauth" in str(request.url):
            return httpx.Response(
                200, json={"access_token": "test-token", "expires_at": 9999999999999}
            )
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"tools":["onsite_now"]}'}}]},
        )

    original = httpx.Client
    monkeypatch.setattr(
        llm.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    gateway = llm.ModelGateway()
    assert gateway.complete(
        "gigachat", "prompt", {}, llm.TOOLS["selection_schema"]
    ) == {"tools": ["onsite_now"]}
    assert len(calls) == 2


def test_assistant_uses_read_only_db_and_rejects_unbacked_claims(monkeypatch):
    @contextmanager
    def db(actor, **kwargs):
        assert kwargs == {"analytics": True}
        yield object()

    monkeypatch.setattr(llm, "transaction", db)
    query = Mock(return_value={"employees": 2, "shifts": 3, "hours": 12.75})
    monkeypatch.setattr(llm, "tool_query", query)
    complete = Mock(
        side_effect=[{"tools": ["period_summary"]}, {"blocks": [{"fact_id": "f1"}]}]
    )
    monkeypatch.setattr(llm.gateway, "complete", complete)
    start = datetime.now(timezone.utc) - timedelta(days=1)
    end = start + timedelta(days=1)
    actor = Actor(uuid4(), frozenset({"manager"}))
    site = uuid4()
    answer = llm.assistant(actor, site, "stat", start, end, None, "ollama")
    assert (
        "12.75" in answer["text"] and query.call_count == 1 and complete.call_count == 2
    )
    complete.side_effect = [
        {"tools": ["period_summary"]},
        {"blocks": [{"fact_id": "forged_number_999"}]},
    ]
    with pytest.raises(ValueError, match="Unsupported model claim"):
        llm.assistant(actor, site, "stat", start, end, None, "ollama")
    complete.side_effect = [{"tools": ["DROP TABLE shifts"]}]
    with pytest.raises(ValueError, match="unsupported tool"):
        llm.assistant(actor, site, "stat", start, end, None, "ollama")
