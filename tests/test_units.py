from app import knowledge, llm
from app.config import Settings
from app.telegram import split_text, to_html
import pytest


def test_prepare_folds_system_and_alternates():
    turns = llm.prepare("SYS", [
        {"role": "assistant", "content": "orphan"},
        {"role": "user", "content": "a"},
        {"role": "user", "content": "b"},
        {"role": "assistant", "content": "c"},
        {"role": "user", "content": "d"},
    ])
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]
    assert turns[0]["content"].startswith("SYS") and turns[0]["content"].endswith("a\n\nb")


def test_only_gemma_models_are_accepted(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")
    with pytest.raises(SystemExit):
        Settings().validate()
    monkeypatch.setenv("LLM_MODEL", "gemma-3-27b-it")
    Settings().validate()


def test_retrieval_finds_the_relevant_chunk():
    filler = [f"Paragraph {i} about gardening, soil and tomatoes. " * 12 for i in range(12)]
    chunks = knowledge.chunk("\n\n".join(filler[:6] + ["The refund window is 30 days from delivery."] + filler[6:]))
    assert len(chunks) > 4
    picked = knowledge.top_chunks("how long is the refund window?", chunks, budget=1500)
    assert any("30 days" in c for c in picked)
    assert sum(len(c) for c in picked) <= 1500


def test_chunking_handles_one_giant_paragraph():
    chunks = knowledge.chunk("word " * 2000)
    assert all(len(c) <= 900 for c in chunks) and len(chunks) > 5


def test_binary_files_are_rejected():
    with pytest.raises(knowledge.UnsupportedFile):
        knowledge.extract_text("photo.jpg", b"\xff\xd8\x00\x00binary")


def test_markdown_to_telegram_html():
    out = to_html("## Title\n**bold** and *soft* with `a<b>` & more\n* item\n```python\nif a < b:\n    pass\n```")
    assert "<b>Title</b>" in out and "<b>bold</b>" in out and "<i>soft</i>" in out
    assert "<code>a&lt;b&gt;</code>" in out and "&amp; more" in out and "• item" in out
    assert "<pre>if a &lt; b:\n    pass</pre>" in out
    assert to_html("2 * 3 * 4 = 24") == "2 * 3 * 4 = 24"


def test_long_messages_are_split():
    parts = split_text("para one.\n\n" + "x" * 5000 + "\n\nend", limit=1000)
    assert all(len(p) <= 1000 for p in parts) and "".join(parts).count("x") == 5000


def test_thinking_is_stripped_from_replies():
    assert llm.strip_thoughts("<thought>plan\nmore</thought>Paris.") == "Paris."
    assert llm.strip_thoughts("<thought>ran out of tokens mid-thou") == ""
    assert llm.strip_thoughts("No thinking here") == "No thinking here"


def test_reasoning_spiral_is_retried_once(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_thinking_tokens", 3000)
    replies = ["", "Paris."]
    budgets = []

    def fake(body):
        budgets.append(body["max_tokens"])
        return replies.pop(0)

    monkeypatch.setattr(llm, "_complete", fake)
    assert llm.chat("", [{"role": "user", "content": "hi"}], max_tokens=100) == "Paris."
    assert budgets == [3100, 3100]

    replies[:] = [""]
    with pytest.raises(llm.LLMError):
        llm.chat("", [{"role": "user", "content": "hi"}], max_tokens=8, retry_empty=False)
