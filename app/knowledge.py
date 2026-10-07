"""Agent knowledge: text extraction, chunking and lexical (BM25) retrieval.

Retrieval is deliberately embedding-free so the whole platform needs exactly
one model (Gemma) and no vector database.
"""
import io
import math
import re
from collections import Counter

_WORD = re.compile(r"\w+", re.UNICODE)


class UnsupportedFile(Exception):
    pass


def extract_text(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception:
            raise UnsupportedFile("That PDF couldn't be read.")
        if not text.strip():
            raise UnsupportedFile("That PDF has no selectable text (it may be a scan).")
        return text
    if b"\x00" in data[:4096]:
        raise UnsupportedFile("Only text, Markdown and PDF files are supported.")
    return data.decode("utf-8", errors="replace")


def chunk(text: str, size: int = 900) -> list[str]:
    """Pack paragraphs into chunks of roughly `size` characters."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    for p in paragraphs:
        while len(p) > size:
            cut = p.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            pieces.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            pieces.append(p)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > size:
            chunks.append(current)
            current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def tokenize(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def top_chunks(query: str, chunks: list[str], budget: int = 3200) -> list[str]:
    """Return the chunks most relevant to `query`, within a character budget."""
    if not chunks:
        return []
    if sum(len(c) for c in chunks) <= budget:
        return chunks

    docs = [tokenize(c) for c in chunks]
    avg_len = sum(len(d) for d in docs) / len(docs) or 1
    df: Counter = Counter()
    for d in docs:
        df.update(set(d))
    terms = set(tokenize(query))
    k1, b = 1.5, 0.75
    scores = []
    for d in docs:
        tf = Counter(d)
        score = 0.0
        for t in terms:
            if t in tf:
                idf = math.log(1 + (len(docs) - df[t] + 0.5) / (df[t] + 0.5))
                score += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avg_len))
        scores.append(score)

    ranked = sorted(range(len(chunks)), key=lambda i: (-scores[i], i))
    if scores[ranked[0]] == 0:
        ranked = list(range(len(chunks)))  # nothing matched: fall back to the opening
    picked, used = [], 0
    for i in ranked:
        if picked and scores[i] == 0 and scores[ranked[0]] > 0:
            break
        if used + len(chunks[i]) > budget:
            if picked:
                break
            picked.append(i)
            break
        picked.append(i)
        used += len(chunks[i])
    return [chunks[i][:budget] for i in sorted(picked)]
