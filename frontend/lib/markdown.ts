/**
 * Light Markdown for agent replies, as an HTML string. The text is escaped
 * first, so the only tags in the result are the ones added here.
 */
export function renderReply(text: string): string {
  const escaped = text.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c] as string);
  return escaped
    .split("```")
    .map((part, i) =>
      i % 2
        ? `<pre>${part.replace(/^[\w+#.-]*\n/, "").trim()}</pre>`
        : part
            .replace(/`([^`\n]+)`/g, "<code>$1</code>")
            .replace(/https?:\/\/[^\s<]+[^\s<.,;:!?)\]'"]/g, '<a href="$&" target="_blank" rel="noopener noreferrer">$&</a>')
            .replace(/^\s{0,3}#{1,6}\s+(.+)$/gm, "<b>$1</b>")
            .replace(/^(\s*)[*-]\s+/gm, "$1• ")
            .replace(/\*\*(?=\S)(.+?)(?<=\S)\*\*/g, "<b>$1</b>")
            .replace(/(^|[^*\w])\*(?=[^\s*])([^*\n]+?)\*(?![*\w])/g, "$1<i>$2</i>"),
    )
    .join("");
}
