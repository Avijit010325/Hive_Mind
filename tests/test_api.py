from fastapi.testclient import TestClient

from app import main


def test_signup_gives_starter_agent_and_session(client, user):
    assert user["email"] == "ada@example.com"
    assert client.get("/api/me").json()["email"] == "ada@example.com"
    agents = client.get("/api/agents").json()
    assert [a["name"] for a in agents] == ["Sidekick"]


def test_auth_errors(client, user):
    assert client.post("/api/auth/signup", json={"email": "ada@example.com", "password": "another pass"}).status_code == 409
    assert client.post("/api/auth/signup", json={"email": "nope", "password": "another pass"}).status_code == 422
    short = client.post("/api/auth/signup", json={"email": "b@example.com", "password": "short"})
    assert short.status_code == 422 and isinstance(short.json()["detail"], str)
    client.post("/api/auth/logout")
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/auth/login", json={"email": "ada@example.com", "password": "wrong password"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "ADA@example.com", "password": "correct horse"}).status_code == 200
    assert client.get("/api/me").status_code == 200


def test_agent_crud_and_validation(client, user):
    r = client.post("/api/agents", json={"name": "Coder", "emoji": "🧑‍💻", "role": "code", "instructions": "You code."})
    assert r.status_code == 200
    agent_id = r.json()["id"]
    assert client.post("/api/agents", json={"name": "coder"}).status_code == 409
    assert client.post("/api/agents", json={"name": "bad*name"}).status_code == 422
    r = client.patch(f"/api/agents/{agent_id}", json={"name": "Coder", "role": "writes code"})
    assert r.status_code == 200 and r.json()["role"] == "writes code"
    assert client.delete(f"/api/agents/{agent_id}").status_code == 200
    assert len(client.get("/api/agents").json()) == 1


def test_users_cannot_touch_each_others_agents(client, user):
    agent_id = client.get("/api/agents").json()[0]["id"]
    with TestClient(main.app) as other:
        other.post("/api/auth/signup", json={"email": "eve@example.com", "password": "password123"})
        assert other.get(f"/api/agents/{agent_id}/docs").status_code == 404
        assert other.patch(f"/api/agents/{agent_id}", json={"name": "Mine"}).status_code == 404
        assert other.delete(f"/api/agents/{agent_id}").status_code == 404
        assert other.post(f"/api/agents/{agent_id}/chat", json={"message": "hi"}).status_code == 404


def test_chat_uses_instructions_knowledge_memory_and_history(client, user, gemma):
    agent_id = client.get("/api/agents").json()[0]["id"]
    client.post(f"/api/agents/{agent_id}/docs", json={"title": "Wifi", "text": "The office wifi password is tangerine42."})
    client.post("/api/memory", json={"text": "Lives in Kolkata"})
    gemma.answers = ["It's tangerine42.", "You're welcome."]

    r = client.post(f"/api/agents/{agent_id}/chat", json={"message": "what is the wifi password?"})
    assert r.json() == {"reply": "It's tangerine42.", "steps": []}
    system = gemma.calls[0]["system"]
    assert "Sidekick" in system and "tangerine42" in system and "Lives in Kolkata" in system

    client.post(f"/api/agents/{agent_id}/chat", json={"message": "thanks"})
    turns = gemma.calls[1]["messages"]
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]
    assert len(client.get(f"/api/agents/{agent_id}/chat").json()) == 4

    client.delete(f"/api/agents/{agent_id}/chat")
    assert client.get(f"/api/agents/{agent_id}/chat").json() == []


def test_chat_reports_model_failure_without_saving(client, user, gemma):
    agent_id = client.get("/api/agents").json()[0]["id"]
    gemma.error = "Gemma is rate-limited right now."
    r = client.post(f"/api/agents/{agent_id}/chat", json={"message": "hi"})
    assert r.status_code == 503 and "rate-limited" in r.json()["detail"]
    assert client.get(f"/api/agents/{agent_id}/chat").json() == []


def test_draft_agent_parses_messy_json(client, user, gemma):
    gemma.answers = ['```json\n{"name": "Profe!", "emoji": "🇪🇸", "role": "Strict Spanish tutor", "instructions": "You are a strict Spanish tutor who corrects every mistake."}\n```']
    draft = client.post("/api/agents/draft", json={"description": "a strict spanish tutor"}).json()
    assert draft["name"] == "Profe" and draft["emoji"] == "🇪🇸" and "Spanish" in draft["instructions"]

    gemma.answers = ["Sure! Here is your agent."]  # no JSON at all
    draft = client.post("/api/agents/draft", json={"description": "a strict spanish tutor"}).json()
    assert draft["name"] == "Agent" and "strict spanish tutor" in draft["instructions"]


def test_upload_and_delete_doc(client, user):
    agent_id = client.get("/api/agents").json()[0]["id"]
    r = client.post(f"/api/agents/{agent_id}/docs/upload", files={"file": ("notes.md", b"# Notes\n\nBuy oat milk.", "text/markdown")})
    assert r.status_code == 200 and r.json()["title"] == "notes.md"
    bad = client.post(f"/api/agents/{agent_id}/docs/upload", files={"file": ("x.bin", b"\x00\x01\x02", "application/octet-stream")})
    assert bad.status_code == 415
    assert client.delete(f"/api/docs/{r.json()['id']}").status_code == 200
    assert client.get(f"/api/agents/{agent_id}/docs").json() == []


def test_telegram_not_configured(client, user):
    state = client.get("/api/telegram").json()
    assert state == {"configured": False, "bot": "", "links": []}
    assert client.post("/api/telegram/link").status_code == 503


def test_api_only_serves_the_api(client):
    assert client.get("/healthz").json() == {"ok": True}
    assert client.get("/").status_code == 404  # the UI lives in the Next.js app


def test_voice_interview_builds_and_saves_an_agent(client, user, gemma):
    gemma.answers = ['{"name": "Sidekick", "emoji": "🧘", "role": "Calm meditation guide", "instructions": "You are a calm meditation guide. You speak slowly and never rush the user."}']
    answers = [
        {"question": "What should this agent help you with?", "answer": "guide me through meditation"},
        {"question": "How should it talk to you?", "answer": "calm and slow"},
        {"question": "Anything it should always or never do?", "answer": "never rush me"},
        {"question": "Do you want to give it a name?", "answer": "skip"},
    ]
    r = client.post("/api/agents/interview", json={"answers": answers})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Sidekick 2"  # clashes with the starter agent, so made unique
    prompt = gemma.calls[0]["messages"][0]["content"]
    assert "never rush me" in prompt and "calm and slow" in prompt
    assert [a["name"] for a in client.get("/api/agents").json()] == ["Sidekick", "Sidekick 2"]


def test_voice_interview_needs_a_real_first_answer_and_reports_model_failure(client, user, gemma):
    empty = client.post("/api/agents/interview", json={"answers": [{"question": "q", "answer": " "}]})
    assert empty.status_code == 422 and isinstance(empty.json()["detail"], str)
    gemma.error = "Gemma is rate-limited right now."
    failed = client.post("/api/agents/interview", json={"answers": [{"question": "q", "answer": "a tutor"}]})
    assert failed.status_code == 503
    assert len(client.get("/api/agents").json()) == 1  # nothing half-created


def test_login_attempts_are_limited_per_account(client, user):
    client.post("/api/auth/logout")
    wrong = {"email": "ada@example.com", "password": "wrong password"}
    assert [client.post("/api/auth/login", json=wrong).status_code for _ in range(10)] == [401] * 10
    assert client.post("/api/auth/login", json=wrong).status_code == 429
    # even the right password waits out the window, but other accounts are unaffected
    assert client.post("/api/auth/login", json={"email": "ada@example.com", "password": "correct horse"}).status_code == 429
    assert client.post("/api/auth/login", json={"email": "bob@example.com", "password": "whatever1"}).status_code == 401
