// Cloudflare Worker entry point (see wrangler.jsonc).
// /api/* is forwarded to Render by the same code the Pages Function uses; everything else is the
// static web app from frontend/.
import { onRequest as proxyApi } from "../functions/api/[[path]].js";

export default {
  async fetch(request, env, ctx) {
    const { pathname } = new URL(request.url);
    if (pathname === "/api" || pathname.startsWith("/api/")) {
      return proxyApi({ request, env, ctx });
    }
    return env.ASSETS.fetch(request);
  },
};
