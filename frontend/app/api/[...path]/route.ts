import type { NextRequest } from "next/server";
import { API, forward } from "@/lib/upstream";

/**
 * Same-origin gateway to the FastAPI backend. The browser only ever talks to
 * this app, so the session cookie stays first-party, and the backend is never
 * exposed directly.
 */
export const dynamic = "force-dynamic";

// An agent using tools can work for a few minutes before it answers.
const TIMEOUT_MS = 290_000;

async function handle(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return forward(request, `${API}/api/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`, TIMEOUT_MS);
}

export { handle as GET, handle as POST, handle as PATCH, handle as PUT, handle as DELETE };
