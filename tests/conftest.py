import os
import tempfile

# Configure the app before any of its modules are imported.
_dir = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_dir}/test.db"
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-hs256"
os.environ["GEMINI_API_KEY"] = "test"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["PUBLIC_URL"] = "http://crew.test"
os.environ["TOOLS_ENABLED"] = "false"  # tool tests switch it on with fakes

import pytest
from fastapi.testclient import TestClient

from app import llm, main
from app.db import Base, engine


class FakeGemma:
    """Stands in for the model: records prompts and returns scripted answers."""

    def __init__(self):
        self.calls = []
        self.answers = []
        self.error = None

    def __call__(self, system, messages, max_tokens=700, temperature=0.7, retry_empty=True):
        self.calls.append({"system": system, "messages": messages})
        if self.error:
            raise llm.LLMError(self.error)
        return self.answers.pop(0) if self.answers else "ok"


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    main._attempts.clear()
    yield


@pytest.fixture
def gemma(monkeypatch):
    fake = FakeGemma()
    monkeypatch.setattr(llm, "chat", fake)
    return fake


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def user(client):
    r = client.post("/api/auth/signup", json={"email": "Ada@Example.com", "password": "correct horse"})
    assert r.status_code == 200, r.text
    return r.json()
