// Cloudflare Pages Function: forwards every /api/* request to the API on Render.
//
// The browser only ever talks to the Pages site, so the login cookie stays first-party
// (SameSite=Strict works and no CORS is needed). The API trusts the client IP below only because
// this proxy also sends the shared secret, which the browser never sees.
//
// Pages project settings > Environment variables:
//   API_ORIGIN           e.g. https://jobfinder-api.onrender.com  (no trailing slash)
//   PROXY_SHARED_SECRET  the same value as on Render

const HOP_BY_HOP = ["connection", "keep-alive", "transfer-encoding", "upgrade", "host"];

export async function onRequest({ request, env }) {
  if (!env.API_ORIGIN || !env.PROXY_SHARED_SECRET) {
    return json({ detail: "The API proxy isn't configured yet (API_ORIGIN, PROXY_SHARED_SECRET)." }, 500);
  }

  const incoming = new URL(request.url);
  const target = new URL(incoming.pathname + incoming.search, env.API_ORIGIN);

  const headers = new Headers(request.headers);
  for (const h of HOP_BY_HOP) headers.delete(h);
  // Never let the browser set these itself.
  headers.delete("x-proxy-secret");
  headers.delete("x-client-ip");
  headers.set("X-Proxy-Secret", env.PROXY_SHARED_SECRET);
  headers.set("X-Client-IP", request.headers.get("CF-Connecting-IP") || "");
  headers.set("X-Forwarded-Proto", "https");

  const init = { method: request.method, headers, redirect: "manual" };
  if (!["GET", "HEAD"].includes(request.method)) init.body = request.body;

  let upstream;
  try {
    // A sleeping Render free instance takes up to a minute to wake; fetch waits for it.
    upstream = await fetch(target, init);
  } catch {
    return json({ detail: "The server is waking up. Please try again in a moment." }, 503, { "Retry-After": "10" });
  }

  // Pass the response through unchanged (including Set-Cookie and redirects to signed CV links).
  const response = new Response(upstream.body, upstream);
  response.headers.set("Cache-Control", "no-store");
  return response;
}

function json(body, status, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...extra },
  });
}
