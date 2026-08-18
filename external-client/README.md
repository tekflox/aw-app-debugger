# External client helper

`app-api-client.js` is a vanilla-JS, zero-dependency, no-build-step helper
for calling your app's own backend API **from outside the app** — a browser
extension, a native mobile app, a userscript, or any other caller that hits
your app by absolute URL instead of loading your app's page and doing
same-origin `fetch()`.

Copy this file (not import it — there's no package, and the whole point is
that it has to run with no bundler) into whatever external client you're
building, and rename `appSlug` to your app's own manifest `id`.

See the file's own header comment for the full explanation of why both
hostname shapes exist and how the helper picks between them. Quick version:

```js
const url = buildAppApiUrl(userConfiguredHost, "myapp", "/health");
const resp = await fetch(url, { credentials: "include" });
```

`userConfiguredHost` is whatever the user typed into your client's settings
— it works whether they typed the workspace-wide API host or your app's own
per-app subdomain; `buildAppApiUrl` builds the right path for either.

For a real, previously-shipped example of this exact pattern (including the
bug it fixes), see `aw-app-proxy`'s `extensions/aw-sync-chrome/popup.js` and
`extensions/aw-sync-ios/extension/popup.js`.
