// Placeholder page of the swarm view skeleton: it signs in and prints what
// the server sends. The real page replaces this file.
(() => {
  "use strict";
  const byID = (id) => document.getElementById(id);
  const out = byID("out");
  const status = byID("status");
  const show = (value) => { out.textContent = JSON.stringify(value, null, 2); };

  async function signIn(token) {
    const response = await fetch("/api/auth/login", {
      method: "POST", cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    return response.ok;
  }

  async function start() {
    const fragment = new URLSearchParams(location.hash.slice(1));
    if (fragment.has("token")) {
      await signIn(fragment.get("token"));
      history.replaceState(null, "", location.pathname + location.search);
    }
    const session = await (await fetch("/api/auth/session", { cache: "no-store" })).json();
    if (!session.authenticated) {
      byID("login").hidden = false;
      status.textContent = "sign in";
      return;
    }
    byID("login").hidden = true;
    const campaign = new URLSearchParams(location.search).get("campaign");
    if (!campaign) {
      status.textContent = "landing";
      show(await (await fetch("/api/swarm/landing", { cache: "no-store" })).json());
      return;
    }
    const source = new EventSource("/api/swarm/stream?campaign=" + encodeURIComponent(campaign));
    source.addEventListener("snapshot", (event) => { status.textContent = "live"; show(JSON.parse(event.data)); });
    source.addEventListener("reset", () => { source.close(); location.reload(); });
    source.onerror = () => { status.textContent = "reconnecting"; };
  }

  byID("login").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (await signIn(byID("token").value)) { start(); }
  });
  start();
})();
