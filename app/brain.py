"""What makes an agent an agent: prompt assembly, routing and drafting."""
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import knowledge, llm, sandbox, tools
from .config import settings
from .db import Agent, Chunk, Doc, Memory, Message, TelegramLink, User, clean

log = logging.getLogger("hivemind.brain")

HISTORY_MESSAGES = 14
HISTORY_CHARS = 6000
NAME_RE = re.compile(r"^[\w][\w \-]{0,31}$", re.UNICODE)

STARTER = {
    "name": "Sidekick",
    "emoji": "✨",
    "role": "Friendly general-purpose assistant for everyday questions and tasks",
    "instructions": (
        "You are a warm, sharp general-purpose assistant. Answer directly, keep it short "
        "unless asked for depth, and ask one clarifying question when a request is ambiguous."
    ),
}


def agents_of(db: Session, user_id: int) -> list[Agent]:
    return list(db.scalars(select(Agent).where(Agent.user_id == user_id).order_by(Agent.id)))


def system_prompt(agent: Agent, memories: list[str], reference: list[str], channel: str) -> str:
    parts = [
        f"You are {agent.name}" + (f", {agent.role.rstrip('.')}." if agent.role else "."),
        f"You are one of the user's personal AI agents on HiveMind, chatting over {channel}. "
        f"Today is {date.today():%A, %d %B %Y}.",
    ]
    if agent.instructions.strip():
        parts.append("## Your instructions\n" + agent.instructions.strip()[:4000])
    if memories:
        parts.append("## What you know about the user\n" + "\n".join(f"- {m}" for m in memories))
    if reference:
        parts.append(
            "## Reference material\nUse this when it is relevant. If it does not cover the "
            "question, say so instead of guessing.\n\n" + "\n\n".join(reference)
        )
    parts.append(
        "## Style\nStay in character. Write like a chat message: concise, no preamble, "
        "plain text with light Markdown at most. Reply in the user's language."
    )
    return "\n\n".join(parts)


def history(db: Session, chat_key: str) -> list[dict]:
    rows = list(
        db.scalars(
            select(Message)
            .where(Message.chat_key == chat_key)
            .order_by(Message.id.desc())
            .limit(HISTORY_MESSAGES)
        )
    )
    out, used = [], 0
    for m in rows:  # newest first
        content = m.content[:2000]
        if used + len(content) > HISTORY_CHARS:
            break
        used += len(content)
        out.append({"role": m.role, "content": content})
    return list(reversed(out))


@dataclass
class Reply:
    text: str
    steps: list[str] = field(default_factory=list)  # what the agent did with its tools, in order


def respond(
    db: Session, user: User, agent: Agent, chat_key: str, text: str, channel: str,
    on_step: Callable[[str], None] | None = None, budget: float = 150,
) -> Reply:
    """Answer `text` as `agent` and record the exchange. Raises llm.LLMError.

    An agent with tools works in a loop: the model either answers, or asks for
    tools, whose results are fed back until it answers. `on_step` is told about
    each tool call as it starts, and `budget` caps the loop in seconds.
    """
    text = clean(text).strip()[:6000]
    memories = [
        m.text
        for m in db.scalars(
            select(Memory).where(Memory.user_id == user.id).order_by(Memory.id.desc()).limit(25)
        )
    ]
    chunks = list(db.scalars(select(Chunk.text).where(Chunk.agent_id == agent.id).order_by(Chunk.id)))
    reference = knowledge.top_chunks(text, chunks)
    past = history(db, chat_key)
    system = system_prompt(agent, list(reversed(memories)), reference, channel)
    use_tools = tools.enabled(agent)
    agent_id, user_id = agent.id, user.id
    db.commit()  # hand the connection back to the pool while the model thinks

    turns = past + [{"role": "user", "content": text}]
    steps: list[str] = []
    links: list[str] = []
    if use_tools:
        system += "\n\n" + tools.instructions(sandbox.available())
        answer = ""
        deadline = time.monotonic() + budget
        for _ in range(settings.max_tool_steps):
            out = llm.chat(system, turns, max_tokens=4096)
            calls = tools.parse(out)
            if not calls:
                answer = out
                break
            results = []
            for call in calls:
                label = tools.describe(call)
                steps.append(label)
                if on_step:
                    on_step(label)
                result = tools.execute(agent_id, call)
                links += re.findall(r"Public link for the user: (\S+)", result)
                results.append(f'<result tool="{call.name}">\n{result}\n</result>')
            turns = turns + [
                {"role": "assistant", "content": tools.compact(out)},
                {"role": "user", "content": "\n\n".join(results)
                    + "\n\nUse more tools if the task is not finished. Otherwise answer the user in plain text."},
            ]
            if time.monotonic() > deadline:
                break
        if not answer:  # out of steps or time: make it wrap up with what it has
            turns = turns + [{"role": "user", "content": "Stop using tools now. Tell the user what you did and the result."}]
            answer = tools.strip_calls(llm.chat(system, turns, max_tokens=900)) or "I ran out of steps before finishing."
        for link in dict.fromkeys(links):  # the user must always get the link, even if the model forgot it
            if link not in answer:
                answer += f"\n\nOpen it here: {link}"
    else:
        answer = llm.chat(system, turns)
    answer = clean(answer)

    db.add(Message(user_id=user_id, agent_id=agent_id, chat_key=chat_key, role="user", content=text))
    db.add(Message(user_id=user_id, agent_id=agent_id, chat_key=chat_key, role="assistant", content=answer))
    db.commit()
    return Reply(answer, steps)


def match_mention(agents: list[Agent], text: str) -> tuple[Agent, str] | None:
    """`@Coder fix this` or `Coder: fix this` addresses an agent directly."""
    stripped = text.lstrip()
    low = stripped.lower()
    for agent in sorted(agents, key=lambda a: -len(a.name)):
        for alias in {agent.name.lower(), agent.name.lower().replace(" ", "")}:
            for prefix in (f"@{alias}", f"{alias}:", f"{alias},"):
                if not low.startswith(prefix):
                    continue
                rest = stripped[len(prefix):]
                if prefix.startswith("@") and rest[:1].isalnum():
                    continue  # "@coderx" is not "@coder"
                rest = rest.lstrip(" :,-\n")
                return agent, rest or "Hello!"
    return None


def route(agents: list[Agent], text: str, last_agent_id: int | None) -> Agent:
    """Ask Gemma which agent in the crew should take this message."""
    if len(agents) == 1:
        return agents[0]
    fallback = next((a for a in agents if a.id == last_agent_id), agents[0])
    roster = "\n".join(f"{i}. {a.name}: {a.role or 'general assistant'}" for i, a in enumerate(agents, 1))
    prompt = (
        "You are a dispatcher for a team of AI agents. Pick the one agent best suited to "
        f"answer the user's message.\n\nAgents:\n{roster}\n\n"
    )
    if last_agent_id and fallback.id == last_agent_id:
        prompt += (
            f"The previous message was handled by {fallback.name}. If the new message is a "
            "follow-up to that conversation, keep the same agent.\n\n"
        )
    prompt += f'User message:\n"""\n{text[:800]}\n"""\n\nReply with only the number of the agent. Do not deliberate.'
    try:
        raw = llm.chat("", [{"role": "user", "content": prompt}], max_tokens=8, temperature=0, retry_empty=False)
    except llm.LLMError:
        return fallback
    m = re.search(r"\d+", raw)
    if m and 1 <= int(m.group()) <= len(agents):
        return agents[int(m.group()) - 1]
    by_name = next((a for a in agents if a.name.lower() in raw.lower()), None)
    return by_name or fallback


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name))


def tidy_draft(data: dict, description: str) -> dict:
    """Coerce whatever the model produced into a usable agent definition."""
    name = re.sub(r"[^\w \-]", "", str(data.get("name") or ""), flags=re.UNICODE).strip()[:32]
    if not name or not valid_name(name):
        name = "Agent"
    emoji = str(data.get("emoji") or "").strip()
    if not emoji or len(emoji) > 8 or emoji.isascii():
        emoji = "🤖"
    role = " ".join(str(data.get("role") or description).split())[:160]
    instructions = str(data.get("instructions") or "").strip()[:4000]
    if len(instructions) < 20:
        instructions = f"You are an AI agent with this job: {description.strip()}"
    return {"name": name, "emoji": emoji, "role": role, "instructions": clean(instructions)}


_DRAFT_FORMAT = (
    "Respond with ONLY a JSON object, no code fences, with exactly these keys:\n"
    '- "name": a short memorable name, 1-2 words, letters only\n'
    '- "emoji": one emoji that fits\n'
    '- "role": one line (max 12 words) saying what the agent is for\n'
)


def _draft(prompt: str, description: str, max_tokens: int = 500) -> dict:
    raw = llm.chat("", [{"role": "user", "content": prompt}], max_tokens=max_tokens, temperature=0.6)
    data: dict = {}
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(raw[start : end + 1])
            if isinstance(parsed, dict):
                data = parsed
        except ValueError:
            log.info("Draft was not valid JSON; using fallbacks")
    return tidy_draft(data, description)


def draft_agent(description: str) -> dict:
    """Turn one sentence into a full agent definition. Raises llm.LLMError."""
    description = clean(description).strip()[:600]
    prompt = (
        f"Design an AI agent from the description below. {_DRAFT_FORMAT}"
        '- "instructions": 4-7 sentences addressed to the agent as "You", covering its '
        "expertise, tone, how it should answer and what it should avoid\n\n"
        f"Description: {description}"
    )
    return _draft(prompt, description)


def draft_from_interview(answers: list[tuple[str, str]]) -> dict:
    """Build an agent from a spoken interview (question, answer pairs). Raises llm.LLMError.

    The answers come from speech recognition, so the model is told to expect
    mishearings and to ignore non-answers such as "skip".
    """
    pairs = [(clean(q).strip()[:200], clean(a).strip()[:1000]) for q, a in answers]
    transcript = "\n".join(f"Q: {q}\nA: {a or '(no answer)'}" for q, a in pairs)
    prompt = (
        "Design an AI agent from this short interview with its future user. The answers were "
        "transcribed from speech, so fix obvious mishearings and ignore non-answers such as "
        f'"skip", "no" or "nothing". {_DRAFT_FORMAT}'
        "  If the user asked for a specific name, use exactly that name.\n"
        '- "instructions": 5-8 sentences addressed to the agent as "You". Cover its purpose, '
        "the tone the user asked for, and every rule they gave about what it must always or "
        "never do. Do not invent requirements the user did not mention.\n\n"
        f"Interview:\n{transcript}"
    )
    return _draft(prompt, pairs[0][1], max_tokens=600)


def unique_name(agents: list[Agent], name: str) -> str:
    taken = {a.name.lower() for a in agents}
    if name.lower() not in taken:
        return name
    for n in range(2, 100):
        candidate = f"{name[:29]} {n}"
        if candidate.lower() not in taken:
            return candidate
    return name


def add_doc(db: Session, agent: Agent, title: str, text: str, limit_chars: int) -> Doc:
    """Store a piece of knowledge for an agent. Raises ValueError when it won't fit."""
    text = clean(text).strip()
    if not text:
        raise ValueError("There is no text in that.")
    used = sum(db.scalars(select(Doc.chars).where(Doc.agent_id == agent.id)))
    if used + len(text) > limit_chars:
        raise ValueError(
            f"That would exceed this agent's knowledge limit ({limit_chars:,} characters)."
        )
    doc = Doc(agent_id=agent.id, title=clean(title).strip()[:160] or "Untitled", chars=len(text))
    db.add(doc)
    db.flush()
    for piece in knowledge.chunk(text):
        db.add(Chunk(doc_id=doc.id, agent_id=agent.id, text=piece))
    db.commit()
    return doc


def delete_agent(db: Session, agent: Agent) -> None:
    db.execute(delete(Chunk).where(Chunk.agent_id == agent.id))
    db.execute(delete(Doc).where(Doc.agent_id == agent.id))
    db.execute(delete(Message).where(Message.chat_key == f"web:{agent.id}"))
    for link in db.scalars(select(TelegramLink).where(TelegramLink.user_id == agent.user_id)):
        if link.active_agent_id == agent.id:
            link.active_agent_id = None
        if link.last_agent_id == agent.id:
            link.last_agent_id = None
    agent_id = agent.id
    db.delete(agent)
    db.commit()
    sandbox.remove(agent_id)
