from datetime import timedelta

import pytest
from sqlalchemy import select

from app import brain
from app.bot import Bot
from app.db import Agent, LinkToken, Memory, SessionLocal, TelegramLink, User, now
from app.telegram import TelegramError

CHAT = 4242


class FakeTelegram:
    def __init__(self):
        self.sent = []
        self.files = {}

    def call(self, method, **params):
        return True

    def send(self, chat_id, text, reply_markup=None):
        self.sent.append({"chat_id": chat_id, "text": text, "markup": reply_markup})

    def download(self, file_id, max_bytes):
        return self.files[file_id]

    @property
    def last(self):
        return self.sent[-1]["text"]


def message(text=None, chat_type="private", **extra):
    msg = {"chat": {"id": CHAT, "type": chat_type}, "from": {"username": "ada"}, **extra}
    if text is not None:
        msg["text"] = text
    return {"update_id": 1, "message": msg}


@pytest.fixture
def bot():
    return Bot(FakeTelegram())


@pytest.fixture
def account():
    with SessionLocal() as db:
        user = User(email="ada@example.com", password_hash="x")
        db.add(user)
        db.flush()
        db.add(Agent(user_id=user.id, name="Coder", emoji="🧑‍💻", role="programming help", instructions="You code."))
        db.add(Agent(user_id=user.id, name="Chef", emoji="🍳", role="recipes and cooking", instructions="You cook."))
        db.add(LinkToken(token="tok123", user_id=user.id, expires_at=now() + timedelta(minutes=5)))
        db.commit()
        return user.id


@pytest.fixture
def linked(bot, account):
    bot.handle_update(message("/start tok123"))
    bot.tg.sent.clear()
    return account


def test_unlinked_chat_is_pointed_to_signup(bot):
    bot.handle_update(message("hello"))
    assert "http://crew.test" in bot.tg.last


def test_link_flow_and_token_is_single_use(bot, account):
    bot.handle_update(message("/start tok123"))
    assert "Connected" in bot.tg.last and "Coder" in bot.tg.last
    with SessionLocal() as db:
        assert db.scalar(select(TelegramLink)).display == "@ada"
        assert db.get(LinkToken, "tok123") is None
    bot.handle_update(message("/start tok123"))
    assert "expired" in bot.tg.last


def test_expired_token_is_rejected(bot, account):
    with SessionLocal() as db:
        db.get(LinkToken, "tok123").expires_at = now() - timedelta(minutes=1)
        db.commit()
    bot.handle_update(message("/start tok123"))
    assert "expired" in bot.tg.last
    with SessionLocal() as db:
        assert db.scalar(select(TelegramLink)) is None


def test_auto_mode_routes_then_answers(bot, linked, gemma):
    gemma.answers = ["2", "Try a dal tadka."]
    bot.handle_update(message("what should I cook tonight?"))
    assert bot.tg.last == "**🍳 Chef**\nTry a dal tadka."
    assert "dispatcher" in gemma.calls[0]["messages"][0]["content"]
    assert "You are Chef" in gemma.calls[1]["system"]


def test_mention_skips_the_router(bot, linked, gemma):
    gemma.answers = ["Use a set."]
    bot.handle_update(message("@coder how do I dedupe a list?"))
    assert len(gemma.calls) == 1
    assert gemma.calls[0]["messages"][-1]["content"] == "how do I dedupe a list?"
    assert bot.tg.last.startswith("**🧑‍💻 Coder**")


def test_pinning_an_agent_with_the_keyboard(bot, linked, gemma):
    bot.handle_update(message("/agents"))
    buttons = bot.tg.sent[-1]["markup"]["inline_keyboard"]
    assert len(buttons) == 3
    chef = next(b[0]["callback_data"] for b in buttons if "Chef" in b[0]["text"])
    bot.handle_update({"update_id": 2, "callback_query": {"id": "q", "data": chef, "message": {"chat": {"id": CHAT}}}})
    assert "Chef pinned" in bot.tg.last
    gemma.answers = ["Salt."]
    bot.handle_update(message("what does python's GIL do?"))
    assert len(gemma.calls) == 1 and "You are Chef" in gemma.calls[0]["system"]
    bot.handle_update(message("/auto"))
    with SessionLocal() as db:
        assert db.scalar(select(TelegramLink)).active_agent_id is None


def test_callback_cannot_pin_someone_elses_agent(bot, linked):
    with SessionLocal() as db:
        other = Agent(user_id=999, name="Spy", role="", instructions="")
        db.add(other)
        db.commit()
        other_id = other.id
    bot.handle_update({"update_id": 2, "callback_query": {"id": "q", "data": f"use:{other_id}", "message": {"chat": {"id": CHAT}}}})
    with SessionLocal() as db:
        assert db.scalar(select(TelegramLink)).active_agent_id is None


def test_hire_from_telegram(bot, linked, gemma):
    gemma.answers = ['{"name": "Chef", "emoji": "🧘", "role": "Calm meditation guide", "instructions": "You are a calm meditation guide who speaks slowly."}']
    bot.handle_update(message("/hire a calm meditation guide"))
    assert "Hired" in bot.tg.last
    with SessionLocal() as db:
        names = [a.name for a in brain.agents_of(db, linked)]
    assert names == ["Coder", "Chef", "Chef 2"]  # clashing name is made unique


def test_memory_commands(bot, linked, gemma):
    bot.handle_update(message("/remember I am vegetarian"))
    bot.handle_update(message("/memory"))
    assert "I am vegetarian" in bot.tg.last
    gemma.answers = ["Paneer."]
    bot.handle_update(message("@chef dinner idea?"))
    assert "I am vegetarian" in gemma.calls[0]["system"]
    bot.handle_update(message("/forget"))
    with SessionLocal() as db:
        assert db.scalar(select(Memory)) is None


def test_sending_a_file_teaches_the_agent(bot, linked, gemma):
    bot.tg.files["f1"] = b"The launch code is 0000."
    doc = {"file_id": "f1", "file_name": "secrets.txt", "file_size": 24}
    bot.handle_update(message(None, document=doc, caption="@chef"))
    assert "Chef" in bot.tg.last and "secrets.txt" in bot.tg.last
    gemma.answers = ["0000"]
    bot.handle_update(message("@chef what is the launch code?"))
    assert "launch code is 0000" in gemma.calls[0]["system"]


def test_model_failure_is_reported_politely(bot, linked, gemma):
    gemma.error = "Gemma is rate-limited right now."
    bot.handle_update(message("@coder hi"))
    assert bot.tg.last == "⚠️ Gemma is rate-limited right now."


def test_group_chats_and_unlink(bot, linked):
    bot.handle_update(message("hello", chat_type="group"))
    assert bot.tg.sent == []
    bot.handle_update(message("/unlink"))
    with SessionLocal() as db:
        assert db.scalar(select(TelegramLink)) is None


def test_crash_in_handler_is_contained(bot, linked, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(brain, "agents_of", boom)
    bot.handle_update(message("hello"))
    assert "Something went wrong" in bot.tg.last
