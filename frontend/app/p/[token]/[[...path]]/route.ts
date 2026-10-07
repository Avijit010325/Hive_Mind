import type { NextRequest } from "next/server";
import { API, forward } from "@/lib/upstream";

/**
 * Preview links: /p/<token>/... shows what an agent is serving from the
 * sandbox. The API adds a `Content-Security-Policy: sandbox` header, so a
 * preview page can never act as the person viewing it.
 */
export const dynamic = "force-dynamic";

async function handle(request: NextRequest, context: { params: Promise<{ token: string }> }) {
  const { token } = await context.params;
  const prefix = `/p/${token}`;
  const pathname = new URL(request.url).pathname;
  const rest = pathname.slice(prefix.length);
  // "/p/<token>" must end in a slash, or the page's relative links resolve one level up.
  if (rest === "") return new Response(null, { status: 308, headers: { Location: `${prefix}/${request.nextUrl.search}` } });
  return forward(request, `${API}/preview/${encodeURIComponent(token)}${rest}${request.nextUrl.search}`, 30_000);
}

export { handle as GET, handle as HEAD, handle as POST };
