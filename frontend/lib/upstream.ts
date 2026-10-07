/** Where the FastAPI backend lives. Read at runtime, so one image works in any deployment. */
export const API = (process.env.API_URL || "http://localhost:8001").replace(/\/$/, "");

const DROP_REQUEST = ["host", "connection", "content-length", "transfer-encoding", "keep-alive", "upgrade"];
const DROP_RESPONSE = ["content-encoding", "content-length", "transfer-encoding", "connection", "keep-alive", "set-cookie"];

/** Forward a request to `target` and hand the response back, cookies included. */
export async function forward(request: Request, target: string, timeoutMs: number): Promise<Response> {
  const headers = new Headers(request.headers);
  DROP_REQUEST.forEach((name) => headers.delete(name));
  const hasBody = !["GET", "HEAD"].includes(request.method);

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (err) {
    const timedOut = err instanceof Error && err.name === "TimeoutError";
    return Response.json(
      { detail: timedOut ? "That took too long. Please try again." : "The server is starting up or unreachable. Please try again in a moment." },
      { status: timedOut ? 504 : 502 },
    );
  }

  const out = new Headers();
  upstream.headers.forEach((value, name) => { if (!DROP_RESPONSE.includes(name)) out.set(name, value); });
  upstream.headers.getSetCookie().forEach((cookie) => out.append("set-cookie", cookie));
  const empty = [204, 205, 304].includes(upstream.status) || request.method === "HEAD";
  return new Response(empty ? null : upstream.body, { status: upstream.status, headers: out });
}
