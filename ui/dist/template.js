function d({ apiUrl: c, wsUrl: r, fetchImpl: s = fetch }) {
  async function e() {
    const t = await s(c("/hello"));
    if (!t.ok) throw new Error(`GET /hello -> ${t.status}`);
    return t.json();
  }
  function o({ onOpen: t, onMessage: l, onClose: p } = {}) {
    const n = new WebSocket(r("/ws/echo"));
    return t && n.addEventListener("open", t), l && n.addEventListener("message", (a) => l(a.data)), p && n.addEventListener("close", p), {
      send: (a) => n.send(a),
      close: () => n.close(),
      raw: n
    };
  }
  return { hello: e, connectEcho: o };
}
const i = "aw-app-template";
function f(c) {
  const s = d({
    apiUrl: (e) => `/api/apps/${i}${e}`,
    wsUrl: (e) => c.sdk.api.wsUrl(`/api/apps/${i}${e}`),
    fetchImpl: (e, o) => c.sdk.api.fetch(e, o)
  }).connectEcho({
    onMessage: (e) => console.debug(`[${i}] echo:`, e)
  });
  c.onDispose(() => s.close());
}
export {
  f as default,
  f as register
};
