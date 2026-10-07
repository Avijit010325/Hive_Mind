"""Telegram worker: long-polls for updates and lets the crew answer.

Run with `python -m app.bot`. Long polling means no public URL or webhook
is needed, so the stack works from a laptop as well as from a server.
"""
import logging
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import brain, knowledge, llm
from .config import settings
from .db import Agent, LinkToken, Memory, Message, SessionLocal, TelegramLink, User, clean, init_db, now
from .telegram import Telegram, TelegramError

log = logging.getLogger("hivemind.bot")

COMMANDS = [
    ("agents", "See your crew and pick who answers"),
    ("auto", "Let the best agent answer each message"),
    ("hire", "Create an agent from one sentence"),
    ("new", "Start a fresh conversation"),
    ("remember", "Save a fact every agent should know"),
    ("memory", "Show what your crew remembers"),
    ("forget", "Wipe the shared memory"),
    ("help", "How this works"),
    ("unlink", "Disconnect this chat"),
]

HELP = (
    "**How your crew works**\n"
    "Just type. In auto mode the best-suited agent answers each message.\n\n"
    "• Talk to one agent directly: `@Name your message`\n"
    "• /agents: see your crew, pin one agent, or go back to auto\n"
    "• /hire `a strict Spanish tutor`: create a new agent from a sentence\n"
    "• Send a .txt, .md or .pdf file to teach it to an agent\n"
    "• /remember `I'm vegetarian`: a fact all agents will know\n"
    "• /memory, /forget: view or wipe shared memory\n"
    "• /new: start a fresh conversation\n"
    "• /unlink: disconnect this chat"
)


class Bot:
    def __init__(self, tg: Telegram):
        self.tg = tg
        self.locks: dict[int, threading.Lock] = defaultdict(threading.Lock)

    # --- plumbing ---------------------------------------------------------

    @contextmanager
    def typing(self, chat_id: int):
        """Show 'typing…' for as long as the model is working."""
        done = threading.Event()

        def pulse():
            while not done.is_set():
                try:
                    self.tg.call("sendChatAction", chat_id=chat_id, action="typing")
                except (TelegramError, httpx.HTTPError):
                    pass
                done.wait(4)

        thread = threading.Thread(target=pulse, daemon=True)
        thread.start()
        try:
            yield
        finally:
            done.set()

    def handle_update(self, update: dict) -> None:
        msg = update.get("message")
        query = update.get("callback_query")
        chat_id = (msg or (query or {}).get("message") or {}).get("chat", {}).get("id")
        if chat_id is None:
            return
        with self.locks[chat_id]:  # one message at a time per chat keeps replies in order
            try:
                with SessionLocal() as db:
                    if msg:
                        self.on_message(db, msg)
                    else:
                        self.on_callback(db, query)
            except Exception:
                log.exception("Failed to handle update %s", update.get("update_id"))
                try:
                    self.tg.send(chat_id, "⚠️ Something went wrong on my side. Please try again.")
                except Exception:
                    pass

    # --- messages ---------------------------------------------------------

    def on_message(self, db: Session, msg: dict) -> None:
        chat = msg["chat"]
        chat_id = chat["id"]
        text = clean(msg.get("text") or msg.get("caption") or "").strip()

        if chat.get("type") != "private":
            if text.startswith("/"):
                self.tg.send(chat_id, "I only work in private chats. Message me directly.")
            return

        cmd, arg = "", ""
        if text.startswith("/"):
            head, _, arg = text.partition(" ")
            cmd, arg = head[1:].split("@")[0].lower(), arg.strip()

        if cmd == "start" and arg:
            return self.link_chat(db, msg, arg)

        link = db.scalar(select(TelegramLink).where(TelegramLink.chat_id == chat_id))
        user = db.get(User, link.user_id) if link else None
        if not link or not user:
            self.tg.send(
                chat_id,
                "👋 This chat isn't connected to a HiveMind account yet.\n\n"
                f"Sign up at {settings.public_url}, then tap **Connect Telegram**. It takes one tap.",
            )
            return

        if msg.get("document"):
            return self.learn_file(db, link, msg, text)
        if not text:
            self.tg.send(chat_id, "I can read text messages and .txt, .md or .pdf files for now.")
            return
        if cmd:
            return self.on_command(db, link, user, cmd, arg)
        self.chat(db, link, user, text)

    def link_chat(self, db: Session, msg: dict, token: str) -> None:
        chat_id = msg["chat"]["id"]
        row = db.get(LinkToken, token[:64])
        if not row or row.expires_at < now():
            if row:
                db.delete(row)
                db.commit()
            self.tg.send(
                chat_id,
                f"That link has expired. Open {settings.public_url} and tap **Connect Telegram** for a fresh one.",
            )
            return
        sender = msg.get("from") or {}
        display = ("@" + sender["username"]) if sender.get("username") else (sender.get("first_name") or "Telegram")
        link = db.scalar(select(TelegramLink).where(TelegramLink.chat_id == chat_id))
        if link is None:
            link = TelegramLink(chat_id=chat_id, user_id=row.user_id)
            db.add(link)
        elif link.user_id != row.user_id:
            # The chat moves to the new account; drop the old account's thread.
            db.execute(delete(Message).where(Message.chat_key == f"tg:{chat_id}"))
            link.active_agent_id = link.last_agent_id = None
        link.user_id = row.user_id
        link.display = clean(display)[:120]
        db.delete(row)
        db.commit()

        agents = brain.agents_of(db, link.user_id)
        crew = "\n".join(f"{a.emoji} **{a.name}**: {a.role}" for a in agents) or "No agents yet. Try /hire."
        self.tg.send(
            chat_id,
            f"✅ Connected! Your crew is ready:\n\n{crew}\n\n"
            "Just type and the right agent answers. /help shows everything else.",
        )

    def chat(self, db: Session, link: TelegramLink, user: User, text: str) -> None:
        chat_id = link.chat_id
        agents = brain.agents_of(db, user.id)
        if not agents:
            self.tg.send(chat_id, "Your crew is empty. Create one with /hire `a friendly assistant`.")
            return
        with self.typing(chat_id):
            mention = brain.match_mention(agents, text)
            pinned = next((a for a in agents if a.id == link.active_agent_id), None)
            if mention:
                agent, text = mention
            elif pinned:
                agent = pinned
            else:
                last_id = link.last_agent_id
                db.commit()  # don't hold a connection while the dispatcher thinks
                agent = brain.route(agents, text, last_id)
            def progress(step: str) -> None:  # tell the user what the agent is doing as it works
                try:
                    self.tg.send(chat_id, step)
                except Exception:
                    log.info("Could not send a progress update to %s", chat_id)

            try:
                reply = brain.respond(db, user, agent, f"tg:{chat_id}", text, "Telegram", on_step=progress, budget=300)
            except llm.LLMError as e:
                self.tg.send(chat_id, f"⚠️ {e}")
                return
        link.last_agent_id = agent.id
        db.commit()
        answer = f"**{agent.emoji} {agent.name}**\n{reply.text}" if len(agents) > 1 else reply.text
        self.tg.send(chat_id, answer)

    def learn_file(self, db: Session, link: TelegramLink, msg: dict, caption: str) -> None:
        chat_id = link.chat_id
        doc = msg["document"]
        agents = brain.agents_of(db, link.user_id)
        if not agents:
            self.tg.send(chat_id, "Create an agent first with /hire, then send the file again.")
            return
        mention = brain.match_mention(agents, caption) if caption else None
        agent = (
            mention[0] if mention
            else next((a for a in agents if a.id == link.active_agent_id), None)
            or next((a for a in agents if a.id == link.last_agent_id), None)
            or agents[0]
        )
        name = doc.get("file_name") or "file"
        if (doc.get("file_size") or 0) > settings.max_doc_bytes:
            self.tg.send(chat_id, f"That file is too big. The limit is {settings.max_doc_bytes // 1_000_000} MB.")
            return
        try:
            with self.typing(chat_id):
                data = self.tg.download(doc["file_id"], settings.max_doc_bytes)
                text = knowledge.extract_text(name, data)
                brain.add_doc(db, agent, name, text, settings.max_knowledge_chars)
        except (knowledge.UnsupportedFile, ValueError) as e:
            db.rollback()
            self.tg.send(chat_id, f"⚠️ {e}")
            return
        hint = "" if len(agents) == 1 else "\nTo teach a different agent, send the file with the caption `@Name`."
        self.tg.send(chat_id, f"📚 {agent.emoji} **{agent.name}** has read {name} and can now answer from it.{hint}")

    # --- commands ---------------------------------------------------------

    def on_command(self, db: Session, link: TelegramLink, user: User, cmd: str, arg: str) -> None:
        chat_id = link.chat_id
        agents = brain.agents_of(db, user.id)

        if cmd == "start":
            self.tg.send(chat_id, "You're already connected. Just type, or see /help.")
        elif cmd == "help":
            self.tg.send(chat_id, HELP)
        elif cmd == "agents":
            self.show_agents(link, agents)
        elif cmd == "auto":
            link.active_agent_id = None
            db.commit()
            self.tg.send(chat_id, "✨ Auto mode: the best-suited agent answers each message.")
        elif cmd == "use":
            agent = next((a for a in agents if a.name.lower() == arg.lower()), None)
            if not agent:
                return self.show_agents(link, agents)
            link.active_agent_id = agent.id
            db.commit()
            self.tg.send(chat_id, f"📌 {agent.emoji} **{agent.name}** will answer everything. /auto to undo.")
        elif cmd == "hire":
            self.hire(db, link, user, agents, arg)
        elif cmd == "new":
            db.execute(delete(Message).where(Message.chat_key == f"tg:{chat_id}"))
            db.commit()
            self.tg.send(chat_id, "🧹 Fresh start. Previous conversation cleared.")
        elif cmd == "remember":
            if not arg:
                self.tg.send(chat_id, "Tell me what to remember, e.g. /remember `I live in Kolkata`")
                return
            db.add(Memory(user_id=user.id, text=arg[:400]))
            db.commit()
            self.tg.send(chat_id, "🧠 Got it. Every agent in your crew knows this now.")
        elif cmd == "memory":
            rows = list(db.scalars(select(Memory).where(Memory.user_id == user.id).order_by(Memory.id)))
            body = "\n".join(f"• {m.text}" for m in rows) or "Nothing yet. Add a fact with /remember."
            self.tg.send(chat_id, f"**Shared memory**\n{body}")
        elif cmd == "forget":
            db.execute(delete(Memory).where(Memory.user_id == user.id))
            db.commit()
            self.tg.send(chat_id, "🧠 Shared memory wiped.")
        elif cmd == "unlink":
            db.execute(delete(Message).where(Message.chat_key == f"tg:{chat_id}"))
            db.delete(link)
            db.commit()
            self.tg.send(chat_id, f"Disconnected. Reconnect any time from {settings.public_url}.")
        else:
            self.tg.send(chat_id, "I don't know that command. /help lists what I can do.")

    def show_agents(self, link: TelegramLink, agents: list[Agent]) -> None:
        if not agents:
            self.tg.send(link.chat_id, "Your crew is empty. Create one with /hire `a friendly assistant`.")
            return
        pinned = link.active_agent_id if any(a.id == link.active_agent_id for a in agents) else None
        lines = [f"{a.emoji} **{a.name}**: {a.role}" for a in agents]
        mode = "✨ Auto mode is on." if not pinned else "📌 One agent is pinned."
        rows = [
            [{"text": f"{'📌 ' if a.id == pinned else ''}{a.emoji} {a.name}", "callback_data": f"use:{a.id}"}]
            for a in agents
        ]
        rows.append([{"text": f"{'✅ ' if not pinned else ''}✨ Auto (best agent per message)", "callback_data": "use:auto"}])
        self.tg.send(
            link.chat_id,
            "**Your crew**\n" + "\n".join(lines) + f"\n\n{mode} Tap to choose who answers:",
            reply_markup={"inline_keyboard": rows},
        )

    def hire(self, db: Session, link: TelegramLink, user: User, agents: list[Agent], description: str) -> None:
        chat_id = link.chat_id
        if len(description) < 4:
            self.tg.send(chat_id, "Describe the agent, e.g. /hire `a strict Spanish tutor who corrects my grammar`")
            return
        if len(agents) >= settings.max_agents:
            self.tg.send(chat_id, f"Your crew is full ({settings.max_agents} agents). Remove one in the dashboard first.")
            return
        db.commit()
        try:
            with self.typing(chat_id):
                draft = brain.draft_agent(description)
        except llm.LLMError as e:
            self.tg.send(chat_id, f"⚠️ {e}")
            return
        draft["name"] = brain.unique_name(agents, draft["name"])
        agent = Agent(user_id=user.id, **draft)
        db.add(agent)
        db.commit()
        self.tg.send(
            chat_id,
            f"🎉 Hired {agent.emoji} **{agent.name}**: {agent.role}\n\n"
            f"Talk to them with `@{agent.name.replace(' ', '')} hello`, or just type and auto mode will "
            f"bring them in when it fits. Fine-tune them at {settings.public_url}.",
        )

    def on_callback(self, db: Session, query: dict) -> None:
        chat_id = query["message"]["chat"]["id"]
        data = query.get("data") or ""
        note = "Done"
        link = db.scalar(select(TelegramLink).where(TelegramLink.chat_id == chat_id))
        if link and data.startswith("use:"):
            choice = data[4:]
            if choice == "auto":
                link.active_agent_id = None
                note = "✨ Auto mode on"
            else:
                agent = db.get(Agent, int(choice)) if choice.isdigit() else None
                if agent and agent.user_id == link.user_id:
                    link.active_agent_id = agent.id
                    note = f"📌 {agent.name} pinned"
                else:
                    note = "That agent no longer exists"
            db.commit()
        try:
            self.tg.call("answerCallbackQuery", callback_query_id=query["id"], text=note)
        except TelegramError:
            pass  # the tap is older than Telegram's answer window
        if link:
            self.tg.send(chat_id, note + ".")

    # --- main loop --------------------------------------------------------

    def run(self) -> None:
        me = self.tg.call("getMe")
        log.info("Bot online as @%s", me.get("username"))
        self.tg.call("deleteWebhook")
        self.tg.call("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS])
        pool = ThreadPoolExecutor(max_workers=settings.bot_workers)
        offset = 0
        while True:
            try:
                updates = self.tg.call(
                    "getUpdates", _timeout=70, offset=offset, timeout=50,
                    allowed_updates=["message", "callback_query"],
                )
            except TelegramError as e:
                if e.code == 409:
                    log.error("Another process is polling with this bot token. Retrying in 15s.")
                    time.sleep(15)
                else:
                    log.warning("getUpdates failed: %s", e)
                    time.sleep(5)
                continue
            except httpx.HTTPError as e:
                log.warning("Telegram unreachable: %s", e)
                time.sleep(5)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                pool.submit(self.handle_update, update)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # request logs would leak the bot token
    settings.validate()
    if not settings.telegram_token:
        log.warning("TELEGRAM_BOT_TOKEN is not set. Add it to .env and run `docker compose up -d` again.")
        while True:
            time.sleep(3600)
    init_db()
    tg = Telegram(settings.telegram_token, settings.telegram_api_base)
    while True:
        try:
            Bot(tg).run()
        except TelegramError as e:
            if e.code in (401, 404):
                log.error("Telegram rejected TELEGRAM_BOT_TOKEN. Check the value from @BotFather.")
                time.sleep(60)
            else:
                log.warning("Bot startup failed: %s", e)
                time.sleep(10)
        except httpx.HTTPError as e:
            log.warning("Telegram unreachable at startup: %s", e)
            time.sleep(10)


if __name__ == "__main__":
    main()
