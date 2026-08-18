/**
 * app-api-client.js — generic helper for calling an AW workspace app's own
 * API from an EXTERNAL client: a browser extension, a native mobile app, a
 * userscript, a CLI, anything that hits the app by absolute URL from
 * OUTSIDE the app's own page.
 *
 * You do NOT need this if you're writing code that runs inside your app's
 * own frontend (ui/src/*.js in this template) and does same-origin
 * `fetch("/health")` calls — the browser resolves those against whatever
 * host is currently loaded, so there's nothing to detect. This file only
 * matters when a caller configures/types in a hostname by hand and your
 * code has to build a full URL from it.
 *
 * ── Why this is needed ──────────────────────────────────────────────────
 *
 * Every app installed on an AW workspace is reachable via TWO different
 * hostname shapes, both hitting the exact same backend route/view/auth
 * (aw-workspace's src/apps/runtime.py, `_attach_mount`, registers a
 * `Mount(f"/api/apps/{app_id}")` AND a `Host(f"{app_id}.app.{...}")` for the
 * same guarded ASGI sub-app, for every installed app — this is generic
 * platform behavior, not something your app opts into):
 *
 *   1. Workspace-wide API host — PATH-PREFIXED with your app's slug:
 *      https://api.<workspace-slug>.workspace.aw.tekflox.com/api/apps/<app-slug>/<route>
 *
 *   2. Per-app subdomain — BARE path, Host-header-routed straight to your
 *      app's own sub-application with NO prefix stripped:
 *      https://<app-slug>.app.<workspace-slug>.workspace.aw.tekflox.com/<route>
 *
 * If your client code hardcodes one shape (e.g. always prefixing with
 * `/api/apps/<slug>`) and a user points it at the OTHER host shape, requests
 * 404 silently — there is no `/api/apps/<slug>/...` route registered on the
 * subdomain-routed mount (only bare paths), and no bare route on the
 * workspace-wide API host (only prefixed ones). This bit aw-app-proxy's own
 * browser extensions in production before this helper existed; see that
 * repo's commit "fix(extensions): sync-cookies extensions use wrong path
 * shape on app-subdomain host" for the concrete incident this generalizes.
 *
 * ── Usage ────────────────────────────────────────────────────────────────
 *
 *   const url = buildAppApiUrl(userConfiguredHost, "myapp", "/health");
 *   const resp = await fetch(url, { credentials: "include" });
 *   const data = await resp.json();
 *
 * `userConfiguredHost` is whatever the user typed into your extension's
 * settings/popup — a bare host, a host:port, or a full URL, with either of
 * the two shapes above. Nothing else needs to change in your calling code:
 * always call `buildAppApiUrl`, never build the path yourself.
 *
 * Vanilla JS, zero dependencies, no build step — safe to drop straight into
 * a browser extension popup.js/background.js, a userscript, or any other
 * environment that can't run a bundler.
 */

/**
 * Extract a bare hostname (no scheme, no port, no path) from whatever the
 * user typed: a full URL, a "host:port" pair, or a bare host.
 *
 * @param {string} rawHost
 * @param {string} [fallbackHost] - returned when rawHost is empty.
 * @returns {string}
 */
function hostnameOf(rawHost, fallbackHost) {
  const host = (rawHost || "").trim();
  if (!host) return fallbackHost || "";
  if (/^https?:\/\//i.test(host)) {
    try {
      return new URL(host).hostname;
    } catch (_) {
      return host;
    }
  }
  return host.split(":")[0].split("/")[0];
}

/**
 * True when `rawHost` matches the `<app-slug>.app.<workspace-slug>...`
 * per-app-subdomain shape — i.e. ".app." appears as a labeled subdomain
 * segment. False for the workspace-wide `api.<workspace-slug>...` host (or
 * any other host shape), which needs the path-prefixed route instead.
 *
 * @param {string} rawHost
 * @param {string} [fallbackHost]
 * @returns {boolean}
 */
function isAppSubdomain(rawHost, fallbackHost) {
  return /\.app\./i.test(hostnameOf(rawHost, fallbackHost));
}

/**
 * Build the correctly-shaped absolute URL for `routePath` on
 * `configuredHost`, for app `appSlug` — bare path on the per-app subdomain,
 * `/api/apps/<appSlug>` prefixed everywhere else.
 *
 * @param {string} configuredHost - whatever hostname/URL the caller
 *   configured (e.g. "api.myws.workspace.aw.tekflox.com" or
 *   "myapp.app.myws.workspace.aw.tekflox.com"), with or without a scheme.
 * @param {string} appSlug - the app's manifest `id` (the `id` field in
 *   `aw-app.json`), e.g. "myapp". Only used for the path-prefixed shape —
 *   the per-app subdomain already encodes it in the hostname itself.
 * @param {string} routePath - the route registered by the app's own
 *   backend sub-app (e.g. "/health" or "health" — leading slash optional).
 * @param {string} [fallbackHost] - host to fall back to when
 *   `configuredHost` is empty. Throws if both are empty.
 * @returns {string} absolute URL, ready to pass to `fetch`.
 */
function buildAppApiUrl(configuredHost, appSlug, routePath, fallbackHost) {
  const host = (configuredHost || "").trim();
  const bare = routePath.startsWith("/") ? routePath : `/${routePath}`;
  const path = isAppSubdomain(host, fallbackHost)
    ? bare
    : `/api/apps/${appSlug}${bare}`;

  const effectiveHost = host || fallbackHost || "";
  if (!effectiveHost) {
    throw new Error(
      "buildAppApiUrl: no host configured and no fallbackHost provided"
    );
  }

  if (/^https?:\/\//i.test(effectiveHost)) {
    return effectiveHost.replace(/\/$/, "") + path;
  }
  // No scheme given. Real AW deployments are always https; only treat
  // localhost/127.0.0.1 as plain http (local dev against aw-workspace
  // running outside the normal TLS-terminating edge).
  const scheme = /^(localhost|127\.0\.0\.1)(:|$)/.test(effectiveHost)
    ? "http"
    : "https";
  return `${scheme}://${effectiveHost}${path}`;
}

// ── Example ────────────────────────────────────────────────────────────
//
// Fetching /api/apps/<slug>/health from an external client (e.g. a browser
// extension popup with a "workspace host" text field the user configures):
//
//   const userConfiguredHost = hostInput.value; // whatever they typed
//   const url = buildAppApiUrl(userConfiguredHost, "myapp", "/health");
//   const resp = await fetch(url, { credentials: "include" });
//   if (resp.status === 401) {
//     // not logged in on that host — same auth cookie (aw_id_jwt) gates
//     // both hostname shapes identically.
//   }
//   const data = await resp.json();
//
// The SAME `buildAppApiUrl` call works unchanged whether `userConfiguredHost`
// is "api.myws.workspace.aw.tekflox.com" (→ /api/apps/myapp/health) or
// "myapp.app.myws.workspace.aw.tekflox.com" (→ /health) — that's the whole
// point of the helper.

// Export for Node-based tests / bundlers; harmless no-op in a plain
// <script> or browser-extension context where `module` doesn't exist.
if (typeof module !== "undefined" && module.exports) {
  module.exports = { hostnameOf, isAppSubdomain, buildAppApiUrl };
}
