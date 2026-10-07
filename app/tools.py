"""The tools an agent can call, and the text protocol it calls them with.

Gemma has no reliable native function calling, so tools are requested with a
small tag syntax that also carries raw file contents without any escaping:

    <tool name="write_file" path="site/index.html">
    <!doctype html> ...
    </tool>
"""
import logging
import re
import shlex
from dataclasses import dataclass, field

from . import firecrawl, sandbox
from .config import settings

log = logging.getLogger("hivemind.tools")

TOOL_RE = re.compile(r'<tool\s+name="([a-z_]+)"([^>]*)>(.*?)(?:</tool>|\Z)', re.S)
ATTR_RE = re.compile(r'([a-z_]+)="([^"]*)"')
MAX_CALLS_PER_TURN = 6
MAX_FILE_CHARS = 200_000

# Runs inside the sandbox, so a page fetch can never reach the platform's own network.
FETCH_SCRIPT = r'''
import html.parser, re, sys, urllib.request
class Text(html.parser.HTMLParser):
    skip = 0; out = []
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg", "head"): self.skip += 1
        if tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"): self.out.append("\n")
    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg", "head") and self.skip: self.skip -= 1
    def handle_data(self, data):
        if not self.skip: self.out.append(data)
req = urllib.request.Request(sys.argv[1], headers={"User-Agent": "Mozilla/5.0 (compatible; HiveMind/1.0)"})
with urllib.request.urlopen(req, timeout=20) as r:
    kind = r.headers.get("content-type", "")
    body = r.read(600_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
if "html" in kind:
    p = Text(); p.feed(body); body = "".join(p.out)
print(re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", body)).strip()[:6000])
'''

# Basic same-site crawler, used when Firecrawl is not configured. Also runs in the sandbox.
CRAWL_SCRIPT = r'''
import html.parser, re, sys, urllib.parse, urllib.request
start, limit = sys.argv[1], int(sys.argv[2])
host = urllib.parse.urlparse(start).netloc
class Page(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(); self.skip = 0; self.out = []; self.links = []; self.title = ""; self.in_title = False
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"): self.skip += 1
        if tag == "title": self.in_title = True
        if tag == "a":
            href = dict(attrs).get("href")
            if href: self.links.append(href)
        if tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"): self.out.append("\n")
    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self.skip: self.skip -= 1
        if tag == "title": self.in_title = False
    def handle_data(self, data):
        if self.in_title: self.title += data
        elif not self.skip: self.out.append(data)
queue, seen, done = [start], {start.split("#")[0].rstrip("/")}, 0
while queue and done < limit:
    url = queue.pop(0)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; HiveMind/1.0)"})
        with urllib.request.urlopen(req, timeout=15) as r:
            if "html" not in r.headers.get("content-type", ""): continue
            body = r.read(500_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
    except Exception as e:
        print(f"## (could not read {url}: {type(e).__name__})\n"); continue
    page = Page(); page.feed(body); done += 1
    text = re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", "".join(page.out))).strip()
    print(f"## {page.title.strip() or url}\n{url}\n{text[:int(sys.argv[3])]}\n")
    for href in page.links:
        link = urllib.parse.urljoin(url, href).split("#")[0]
        key = link.rstrip("/")
        if urllib.parse.urlparse(link).netloc == host and link.startswith("http") and key not in seen \
                and not re.search(r"\.(png|jpe?g|gif|svg|pdf|zip|css|js|ico|mp4|webp)$", link, re.I):
            seen.add(key); queue.append(link)
print(f"[{done} page(s) crawled, {len(queue)} more link(s) not followed]")
'''

PAGE_CHARS = 6000        # one page handed to the model
CRAWL_PAGE_CHARS = 1200  # each page of a crawl
MAX_CRAWL_PAGES = 10


@dataclass
class Call:
    name: str
    attrs: dict = field(default_factory=dict)
    body: str = ""


def instructions(has_sandbox: bool) -> str:
    """The part of the system prompt that teaches the tool protocol."""
    can_read = has_sandbox or firecrawl.configured()
    lines = [
        "## Tools",
        "You can act, not just talk. To use a tool, write a tool tag exactly as shown. You may put several "
        "tags in one reply; they run in order. After the tags, stop: you will receive the results and can "
        "then use more tools or answer the user. Never invent a tool result.",
        "",
        '<tool name="web_search">what to look up</tool>  Search the web. Returns titles, links and snippets.',
    ]
    if can_read:
        lines += [
            '<tool name="fetch_url">https://example.com/page</tool>  Read one web page as text.',
            '<tool name="crawl_site" limit="5">https://example.com</tool>  Read several pages of one website by '
            f"following its links (limit is the number of pages, up to {MAX_CRAWL_PAGES}). Use it to learn what a "
            "whole site says; use fetch_url when one page is enough.",
        ]
    if has_sandbox:
        lines += [
            '<tool name="shell">command</tool>  Run a bash command on your Linux computer (Debian, Python 3, '
            "Node.js, git, curl; no root). It starts in your workspace folder, and files there are kept "
            "between conversations. Each command has 120 seconds.",
            '<tool name="write_file" path="site/index.html">file contents</tool>  Create or overwrite a '
            "file in your workspace (path relative to it). Put the raw contents between the tags, with no "
            "escaping and no code fences.",
            '<tool name="serve" directory="site"></tool>  Start a web server for a folder and get a public '
            "link the user can open. For a custom server put its start command between the tags; it must "
            "listen on 0.0.0.0 and the port in the $PORT environment variable.",
            "",
            "Rules: use tools only when they help; answer simple questions directly. Search first when you "
            "need current facts, then read the best result before answering, and give the source link. When "
            "you build a web page, write the files, then call serve, then give the user the link. Use relative "
            'links between files (href="style.css", never "/style.css"). Do not start servers with shell; use '
            "serve. When you are finished, reply in plain text with no tool tags.",
        ]
    else:
        lines += ["", "Rules: search only when you need current or specific facts, and give the source link. "
                  "When you are finished, reply in plain text with no tool tags."]
    return "\n".join(lines)


def parse(reply: str) -> list[Call]:
    calls = []
    for name, raw_attrs, body in TOOL_RE.findall(reply):
        body = body.strip("\n")
        fenced = re.fullmatch(r"\s*```[\w+-]*\n(.*?)\n?```\s*", body, re.S)  # models like to fence file contents
        if fenced:
            body = fenced.group(1)
        calls.append(Call(name, dict(ATTR_RE.findall(raw_attrs)), body))
    return calls[:MAX_CALLS_PER_TURN]


def strip_calls(reply: str) -> str:
    """The reply without any tool tags (for a final answer that still contains some)."""
    return TOOL_RE.sub("", reply).strip()


def compact(reply: str) -> str:
    """The model's own turn, with file bodies elided so they are not sent back every step."""

    def shrink(match: re.Match) -> str:
        name, attrs, body = match.groups()
        if name == "write_file" and len(body) > 200:
            body = f"[{len(body)} characters, written]"
        return f'<tool name="{name}"{attrs}>{body}</tool>'

    return TOOL_RE.sub(shrink, reply)


def describe(call: Call) -> str:
    """One short line telling the user what the agent is doing."""
    first = (call.body.strip().splitlines() or [""])[0][:80]
    if call.name == "web_search":
        return f"🌐 Searching: {first}"
    if call.name == "fetch_url":
        return f"📖 Reading {first}"
    if call.name == "crawl_site":
        return f"🕸 Crawling {first}"
    if call.name == "shell":
        return f"💻 Running: {first}"
    if call.name == "write_file":
        return f"📝 Writing {call.attrs.get('path', 'a file')}"
    if call.name == "serve":
        return "🚀 Starting the web server"
    return f"🔧 {call.name}"


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "\n… [page continues]"


def _search(query: str) -> str:
    query = query.strip()[:300]
    if not query:
        return "Error: empty search query."
    hits: list[dict] = []
    if firecrawl.configured():
        try:
            hits = firecrawl.search(query)
        except firecrawl.FirecrawlError as e:
            log.info("Firecrawl search failed, using the fallback: %s", e)
    if not hits:
        try:
            from ddgs import DDGS

            hits = [{"title": h.get("title", ""), "url": h.get("href", ""), "description": h.get("body", "")}
                    for h in DDGS().text(query, max_results=5)]
        except Exception as e:
            log.info("Search failed: %s", e)
            return "Error: web search is not available right now."
    if not hits:
        return "No results."
    return "\n\n".join(f"{h['title']}\n{h['url']}\n{h['description'][:300]}" for h in hits[:5])


def _read(agent_id: int, url: str) -> str:
    """One page as text: Firecrawl when configured (renders JavaScript), else the sandbox fetcher."""
    if not re.match(r"https?://", url):
        return "Error: give a full http(s) URL."
    problem = ""
    if firecrawl.configured():
        try:
            page = firecrawl.scrape(url)
            if page["markdown"].strip():
                return f"# {page['title']}\n{page['url']}\n\n{_clip(page['markdown'], PAGE_CHARS)}"
            problem = "The page had no readable content."
        except firecrawl.FirecrawlError as e:
            problem = str(e)
    if not sandbox.available():
        return f"Error: could not read that page. {problem}".strip()
    code, out = sandbox.run(agent_id, f"python3 - {shlex.quote(url)} <<'PY'\n{FETCH_SCRIPT}\nPY", 40, limit=PAGE_CHARS + 500)
    return out if code == 0 and out else f"Error: could not read that page.\n{out[-400:]}"


def _crawl(agent_id: int, url: str, limit: str) -> str:
    """Several pages of one site: Firecrawl when configured, else a basic same-site crawler."""
    if not re.match(r"https?://", url):
        return "Error: give a full http(s) URL."
    pages = min(max(int(limit) if str(limit).isdigit() else 5, 1), MAX_CRAWL_PAGES)
    problem = ""
    if firecrawl.configured():
        try:
            found = firecrawl.crawl(url, pages)
            body = "\n\n".join(f"## {p['title'] or p['url']}\n{p['url']}\n{_clip(p['markdown'], CRAWL_PAGE_CHARS)}" for p in found)
            return f"{body}\n\n[{len(found)} page(s) crawled]"
        except firecrawl.FirecrawlError as e:
            problem = str(e)
    if not sandbox.available():
        return f"Error: could not crawl that site. {problem}".strip()
    command = f"python3 - {shlex.quote(url)} {pages} {CRAWL_PAGE_CHARS} <<'PY'\n{CRAWL_SCRIPT}\nPY"
    code, out = sandbox.run(agent_id, command, 110, limit=pages * (CRAWL_PAGE_CHARS + 300) + 500)
    return out if code == 0 and out else f"Error: could not crawl that site.\n{out[-400:]}"


def execute(agent_id: int, call: Call) -> str:
    """Run one tool call and return its result as text for the model. Never raises."""
    body = call.body.strip()
    try:
        if call.name == "web_search":
            return _search(body)
        if call.name == "fetch_url":
            return _read(agent_id, body)
        if call.name == "crawl_site":
            return _crawl(agent_id, body, call.attrs.get("limit", "5"))
        if call.name not in ("shell", "write_file", "serve"):
            return f"Error: there is no tool called {call.name}."
        if not sandbox.available():
            return "Error: this tool needs the sandbox computer, which is not available on this server."
        if call.name == "shell":
            if not body:
                return "Error: empty command."
            code, out = sandbox.run(agent_id, body, 120)
            return f"exit code {code}\n{out}" if out else f"exit code {code} (no output)"
        if call.name == "write_file":
            if len(call.body) > MAX_FILE_CHARS:
                return "Error: that file is too large."
            path = sandbox.write_file(agent_id, call.attrs.get("path", ""), call.body + "\n")
            return f"Wrote {path} ({len(call.body)} characters)."
        if call.name == "serve":
            url = sandbox.serve(agent_id, call.attrs.get("directory", "."), body)
            return f"The server is running. Public link for the user: {url}"
        return f"Error: there is no tool called {call.name}."
    except sandbox.SandboxError as e:
        return f"Error: {e}"
    except Exception:
        log.exception("Tool %s failed", call.name)
        return "Error: the tool failed unexpectedly."


def enabled(agent) -> bool:
    return bool(settings.tools_enabled and agent.tools)
