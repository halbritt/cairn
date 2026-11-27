// Cairn swarm view (docs/swarm-view.md). One page per campaign, about the
// agents. The page reads the stream and the read routes of `cairn swarm
// serve` and performs only the operator actions that the server offers. It has
// no control that starts, stops or focuses a session, sends keys, manages a
// worktree, triggers compaction or selects a model.
//
// Every piece of text from the server is set with textContent; no server text
// is ever parsed as HTML.
(() => {
  "use strict";

  const byID = (id) => document.getElementById(id);
  const planeIdentifier = /\b[A-Z][A-Z0-9]{1,11}-[0-9]{1,6}\b/g;
  const openDisplays = new Set(["pending", "leased", "held"]);
  // Each delivery state has its own mark and word, so no state relies on colour.
  const displayMarks = {
    pending: "◌", leased: "◐", held: "⏸", settled: "✓", superseded: "↷",
    ignored: "–", failed: "✗", cancelled: "⊘", expired: "⌛",
  };

  const state = {
    csrf: "",
    planeURL: "",
    collection: "",
    campaign: new URLSearchParams(location.search).get("campaign") || "",
    hello: null,
    snapshot: null,
    herdr: null,
    paneTick: null,
    window: null, // context-window observation read from harness transcripts
    windowTick: null,
    resets: [], // reset codes received, newest last (read by the fixture)
    faults: 0,
    cursorResets: 0, // cursor restarts since the last snapshot
    dbObservedAt: null,
    fault: null,
    log: new Map(), // event_id -> SwarmEvent; one entry per event, never two
    logWindow: 300,
    cursor: "",
    source: null,
    received: false, // a message arrived on the current stream
    failures: 0,
    lastMessageAt: 0,
    filter: "",
    detailMember: "",
    eventDetail: "",
    landingTimer: null,
  };

  // Retries reuse the request UUID of the attempt they repeat.
  const pendingRequests = new Map();

  // ------------------------------------------------------------------ helpers

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "dataset") Object.assign(node.dataset, value);
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : String(value));
    }
    append(node, children);
    return node;
  }

  function append(node, children) {
    for (const child of children.flat(Infinity)) {
      if (child === undefined || child === null || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function uuid() {
    if (crypto.randomUUID) return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  function requestID(key) {
    if (!pendingRequests.has(key)) pendingRequests.set(key, uuid());
    return pendingRequests.get(key);
  }

  function parseTime(value) {
    if (!value) return null;
    const time = typeof value === "number" ? value : Date.parse(value);
    return Number.isNaN(time) || time <= 0 ? null : time;
  }

  function age(value) {
    const time = typeof value === "number" ? value : parseTime(value);
    if (time === null) return "unknown";
    let seconds = Math.round((Date.now() - time) / 1000);
    const future = seconds < 0;
    seconds = Math.abs(seconds);
    let text;
    if (seconds < 60) text = `${seconds}s`;
    else if (seconds < 3600) text = `${Math.floor(seconds / 60)}m`;
    else if (seconds < 86400) text = `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
    else text = `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`;
    return future ? `in ${text}` : `${text} ago`;
  }

  function timeEl(value) {
    const time = parseTime(value);
    if (time === null) return el("span", { class: "muted", text: "unknown" });
    return el("time", { datetime: new Date(time).toISOString(), title: new Date(time).toLocaleString(), dataset: { age: String(time) }, text: age(time) });
  }

  function bytes(value) {
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
    return `${(value / 1024 / 1024).toFixed(1)} MiB`;
  }

  // Text that may contain tracker identifiers. With a link template the
  // identifier becomes a link; without one it stays plain text.
  function linked(text) {
    const span = el("span", { class: "text" });
    const value = String(text ?? "");
    if (!state.planeURL) {
      span.textContent = value;
      return span;
    }
    let last = 0;
    for (const match of value.matchAll(planeIdentifier)) {
      append(span, [value.slice(last, match.index), planeLink(match[0])]);
      last = match.index + match[0].length;
    }
    append(span, [value.slice(last)]);
    return span;
  }

  // Long reported text stays readable: the start is shown, the rest on request.
  function longText(text, limit = 180) {
    const value = String(text ?? "");
    if (value.length <= limit) return linked(value);
    return el("details", { class: "long" }, el("summary", {}, linked(value.slice(0, limit) + "…")), linked(value));
  }

  function planeLink(id) {
    if (!state.planeURL) return el("span", { class: "issue", text: id });
    const href = state.planeURL.split("{id}").join(encodeURIComponent(id));
    return el("a", { class: "issue", href, target: "_blank", rel: "noopener noreferrer", text: id });
  }

  function copyButton(command, label) {
    const button = el("button", { type: "button", class: "copy", "aria-label": `${label || "Copy"}: ${command}`, text: label || "Copy" });
    button.addEventListener("click", async () => {
      let done = false;
      try {
        await navigator.clipboard.writeText(command);
        done = true;
      } catch (_) {
        done = false;
      }
      button.textContent = done ? "Copied" : "Copy failed; select the text";
      setTimeout(() => { button.textContent = label || "Copy"; }, 2000);
    });
    return el("span", { class: "command" }, el("code", { text: command }), " ", button);
  }

  function note(text, kind) {
    return el("p", { class: `note ${kind || ""}`.trim(), text });
  }

  // ----------------------------------------------------------------- network

  class APIError extends Error {
    constructor(message, code, status, body) {
      super(message);
      this.code = code || "";
      this.status = status;
      this.body = body || null;
    }
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body !== undefined) headers.set("Content-Type", "application/json");
    if ((options.method || "GET") !== "GET") headers.set("X-CSRF-Token", state.csrf);
    let response;
    try {
      response = await fetch(path, {
        method: options.method || "GET", headers, cache: "no-store", credentials: "same-origin",
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
      });
    } catch (error) {
      throw new APIError("the swarm view server did not answer: " + error.message, "UNAVAILABLE", 0);
    }
    let body = null;
    try {
      body = await response.json();
    } catch (_) {
      body = null;
    }
    if (!response.ok) {
      if (response.status === 401) showSignIn();
      throw new APIError((body && (body.error || body.message)) || `request failed (${response.status})`, body && body.code, response.status, body);
    }
    return body;
  }

  function post(path, body) {
    return api(path, { method: "POST", body });
  }

  // ----------------------------------------------------------------- sign-in

  async function signIn(token) {
    const reply = await api("/api/auth/login", { method: "POST", body: { token } });
    state.csrf = reply.csrf_token || "";
  }

  function showSignIn(message) {
    closeStream();
    for (const dialog of document.querySelectorAll("dialog[open]")) dialog.close();
    byID("landing").hidden = true;
    byID("campaign").hidden = true;
    byID("signout").hidden = true;
    byID("signin").hidden = false;
    setLive("signed-out", "sign in");
    const error = byID("signin-error");
    error.textContent = message || "";
    error.hidden = !message;
    byID("token-input").focus();
  }

  async function start() {
    const fragment = new URLSearchParams(location.hash.slice(1));
    const token = fragment.get("token");
    if (token !== null) {
      // The token leaves the address bar whether or not it is accepted.
      history.replaceState(null, "", location.pathname + location.search);
      try {
        await signIn(token);
      } catch (error) {
        showSignIn("The token in the address was not accepted: " + error.message);
        return;
      }
    }
    let session;
    try {
      session = await api("/api/auth/session");
    } catch (error) {
      setLive("offline", "server unavailable");
      showFault("The swarm view server did not answer: " + error.message);
      return;
    }
    if (!session.authenticated) {
      showSignIn();
      return;
    }
    state.csrf = session.csrf_token || state.csrf;
    state.planeURL = session.plane_url || "";
    state.collection = session.collection || "";
    byID("signin").hidden = true;
    byID("signout").hidden = false;
    if (state.campaign) startCampaign();
    else startLanding();
  }

  byID("signin").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = byID("token-input");
    try {
      await signIn(input.value.trim());
      input.value = "";
      await start();
    } catch (error) {
      showSignIn("Token not accepted: " + error.message);
    }
  });

  byID("signout").addEventListener("click", async () => {
    try {
      await post("/api/auth/logout", {});
    } catch (_) {
      // The session is gone either way.
    }
    state.csrf = "";
    showSignIn();
  });

  // ----------------------------------------------------------------- landing

  async function startLanding() {
    byID("landing").hidden = false;
    byID("campaign").hidden = true;
    byID("landing-link").hidden = true;
    byID("title").textContent = "Cairn swarm view";
    document.title = "Cairn swarm view";
    await loadLanding();
  }

  async function loadLanding() {
    clearTimeout(state.landingTimer);
    try {
      const landing = await api("/api/swarm/landing");
      hideFault();
      setLive("live", "current");
      renderLanding(landing);
    } catch (error) {
      if (error.status === 401) return;
      setLive("offline", "not current");
      showFault("Could not read the landing page: " + error.message + (error.code ? ` (${error.code})` : ""));
    }
    state.landingTimer = setTimeout(loadLanding, 5000);
  }

  function renderLanding(landing) {
    state.landingLabels = new Map();
    for (const session of landing.unaffiliated || []) state.landingLabels.set("agent/" + session.agent_id, session.label);
    const meta = byID("campaign-meta");
    meta.hidden = false;
    meta.replaceChildren(`Collection ${landing.collection} · read `, timeEl(landing.observed_at));
    const errors = landing.section_errors || {};

    renderNeeds(byID("landing-needs"), landing.needs_you, errors.needs_you, landing.operator_principal);

    const campaigns = byID("campaigns");
    campaigns.replaceChildren();
    if (errors.campaigns) campaigns.append(note("Campaigns unavailable: " + errors.campaigns, "unavailable"));
    else if (!(landing.campaigns || []).length) campaigns.append(note("No active campaign in this collection."));
    else {
      const list = el("ul", { class: "cards" });
      for (const campaign of landing.campaigns) {
        list.append(el("li", { class: "card" },
          el("h3", {}, el("a", { href: "?campaign=" + encodeURIComponent(campaign.campaign), text: campaign.campaign })),
          campaign.description ? el("p", { class: "description" }, linked(campaign.description)) : null,
          el("p", { class: "meta" },
            `root ${campaign.root_label || campaign.root_agent} · ${campaign.active_members} members, ${campaign.online_members} online · started `,
            timeEl(campaign.created_at))));
      }
      campaigns.append(list);
      if (landing.campaigns_truncated) campaigns.append(note("More active campaigns exist than are listed here."));
    }

    const sessions = byID("unaffiliated");
    sessions.replaceChildren();
    if (errors.unaffiliated) sessions.append(note("Sessions unavailable: " + errors.unaffiliated, "unavailable"));
    else if (!(landing.unaffiliated || []).length) sessions.append(note("No online session outside a campaign."));
    else {
      const list = el("ul", { class: "cards" });
      for (const session of landing.unaffiliated) {
        list.append(el("li", { class: "card" },
          el("h3", { text: session.label || session.agent_id }),
          el("p", { class: "meta", text: [session.harness, session.model, session.state, session.project].filter(Boolean).join(" · ") || "no metadata" }),
          session.task_summary ? el("p", {}, linked(session.task_summary)) : null,
          el("p", { class: "meta" }, "started ", timeEl(session.started_at))));
      }
      sessions.append(list);
    }

    // The newest messages that no active campaign claims.
    const events = byID("landing-events");
    events.replaceChildren();
    if (errors.events) events.append(el("li", { class: "empty unavailable", text: "Messages unavailable: " + errors.events }));
    else if (!(landing.events || []).length) events.append(el("li", { class: "empty", text: "No message outside a campaign." }));
    else {
      for (const event of landing.events.slice().sort((a, b) => b.position - a.position)) {
        events.append(el("li", { class: `event event-${event.kind}`, dataset: { event: event.event_id } },
          el("div", { class: "event-head" },
            el("span", { class: "tag kind", text: event.kind }),
            event.action ? el("span", { class: "tag", text: "action " + event.action }) : null,
            event.blocker ? el("span", { class: "tag flag", text: "blocker" }) : null,
            el("span", { class: "route", text: `${senderLabel(event)} → ${destinationLabel(event.to)}` }),
            el("span", { class: "muted" }, " · ", timeEl(event.created_at))),
          el("p", { class: "summary" }, event.summary ? linked(event.summary) : el("span", { class: "muted", text: "no summary" }))));
      }
    }
  }

  // ------------------------------------------------------------------ stream

  function startCampaign() {
    byID("landing").hidden = true;
    byID("campaign").hidden = false;
    byID("landing-link").hidden = false;
    byID("title").textContent = state.campaign;
    document.title = state.campaign + " · Cairn swarm view";
    openStream(state.cursor);
  }

  function closeStream() {
    if (state.source) {
      state.source.close();
      state.source = null;
    }
  }

  function openStream(cursor) {
    closeStream();
    state.received = false;
    let url = "/api/swarm/stream?campaign=" + encodeURIComponent(state.campaign);
    if (cursor) url += "&cursor=" + encodeURIComponent(cursor);
    const source = new EventSource(url);
    state.source = source;
    const on = (name, handler) => source.addEventListener(name, (event) => {
      if (state.source !== source) return;
      state.received = true;
      state.failures = 0;
      state.lastMessageAt = Date.now();
      if (event.lastEventId) state.cursor = event.lastEventId;
      let data = null;
      try {
        data = JSON.parse(event.data);
      } catch (_) {
        return;
      }
      handler(data);
      renderLive();
    });
    on("hello", (data) => {
      state.hello = data;
      if (data.log_window > 0) state.logWindow = data.log_window;
      if (data.plane_url !== undefined) state.planeURL = data.plane_url || "";
      setLive("live", "live");
    });
    on("resync", () => {
      // The log events that follow are a new window and replace the list.
      state.log.clear();
      renderFlow();
    });
    on("log", (data) => {
      if (!data || !data.event_id) return;
      addLog(data);
      renderFlow();
    });
    on("snapshot", (data) => {
      state.cursorResets = 0;
      state.snapshot = data;
      state.dbObservedAt = data.observed_at;
      state.fault = null;
      hideFault();
      renderCampaign();
    });
    on("herdr", (data) => {
      state.herdr = data;
      state.paneTick = data;
      renderAgents();
      renderObservations();
    });
    on("window", (data) => {
      state.window = data;
      state.windowTick = data;
      renderAgents();
      renderNearLimit();
      renderObservations();
    });
    on("tick", (data) => {
      state.dbObservedAt = data.db_observed_at || state.dbObservedAt;
      state.paneTick = data.panes || state.paneTick;
      state.windowTick = data.window || state.windowTick;
      state.fault = null;
      hideFault();
      renderObservations();
    });
    on("fault", (data) => {
      state.faults += 1;
      state.fault = data;
      showFault(`The store could not be read (${data.code || "error"}): ${data.error || ""}. Showing the last snapshot; the server keeps trying.`);
      renderObservations();
    });
    on("reset", (data) => {
      // The server closes the stream after a reset; the code decides what next.
      const code = data.code || "";
      state.resets.push(code);
      closeStream();
      if ((code === "STALE_CURSOR" || code === "INVALID_REQUEST") && state.cursorResets < 1) {
        // The cursor cannot continue: a new window replaces the list. Once:
        // a refusal of a stream with no cursor is not about the cursor.
        state.cursorResets += 1;
        state.cursor = "";
        openStream("");
      } else if (code === "NOT_FOUND") {
        setLive("offline", "no such campaign");
        showMissingCampaign(data.error);
      } else if (code === "UNAUTHENTICATED") {
        showSignIn("The session ended. Sign in again; the page continues where it stopped.");
      } else {
        setLive("stale", "store refused the read");
        showFault(`The store refused the read (${code || "error"}): ${data.error || ""}. Trying again in 15 s.`);
        setTimeout(() => { if (!state.source && state.campaign) openStream(state.cursor); }, 15000);
      }
    });
    source.onerror = () => {
      if (state.source !== source) return;
      if (source.readyState === EventSource.CONNECTING) {
        // The browser reconnects itself and sends Last-Event-ID.
        setLive("reconnecting", "reconnecting");
        return;
      }
      // Closed: the server answered with an error, or refused the session.
      closeStream();
      state.failures += 1;
      setLive("reconnecting", "reconnecting");
      recover();
    };
  }

  async function recover() {
    const attempt = state.failures;
    await new Promise((resolve) => setTimeout(resolve, Math.min(1000 * attempt, 10000)));
    if (state.source) return;
    let session;
    try {
      session = await api("/api/auth/session");
    } catch (error) {
      setLive("offline", "server unavailable");
      state.failures += 1;
      recover();
      return;
    }
    if (!session.authenticated) {
      showSignIn("The server no longer knows this browser. Sign in again; the page continues where it stopped.");
      return;
    }
    state.csrf = session.csrf_token || state.csrf;
    // Continue from the last cursor first. If the cursor is refused twice,
    // start a new window: the list is replaced, nothing is repeated.
    if (attempt >= 3) {
      try {
        await api("/api/swarm/campaign?campaign=" + encodeURIComponent(state.campaign));
      } catch (error) {
        if (error.code === "NOT_FOUND" || error.code === "INVALID_REQUEST") {
          setLive("offline", "not available");
          showFault("This campaign cannot be shown: " + error.message + ` (${error.code})`);
          return;
        }
      }
      state.cursor = "";
    }
    openStream(state.cursor);
  }

  function showMissingCampaign(message) {
    const fault = byID("fault");
    fault.replaceChildren(`The campaign "${state.campaign}" does not exist${message ? " (" + message + ")" : ""}. `,
      el("a", { href: "./", text: "Go to the list of campaigns" }));
    fault.hidden = false;
    byID("campaign").hidden = true;
  }

  // One read outside the stream, so the page shows the store's state now.
  async function refreshSnapshot() {
    if (!state.campaign) {
      // The landing page: an empty campaign is the landing scope.
      if (!byID("landing").hidden) loadLanding();
      return;
    }
    try {
      const snapshot = await api("/api/swarm/campaign?campaign=" + encodeURIComponent(state.campaign));
      if (snapshot && snapshot.schema) {
        state.snapshot = snapshot;
        state.dbObservedAt = snapshot.observed_at;
        renderCampaign();
      }
    } catch (_) {
      // The stream brings the next snapshot.
    }
  }

  function addLog(event) {
    if (state.log.has(event.event_id)) return;
    state.log.set(event.event_id, event);
    if (state.log.size > state.logWindow) {
      const ordered = [...state.log.values()].sort((a, b) => a.position - b.position);
      for (const old of ordered.slice(0, ordered.length - state.logWindow)) state.log.delete(old.event_id);
    }
  }

  function setLive(kind, text) {
    const live = byID("live");
    live.dataset.state = kind;
    live.textContent = text;
  }

  function renderLive() {
    if (!state.source) return;
    if (state.source.readyState === EventSource.OPEN) setLive(state.fault ? "stale" : "live", state.fault ? "live, store unreadable" : "live");
  }

  function showFault(text) {
    const fault = byID("fault");
    fault.textContent = text;
    fault.hidden = false;
  }

  function hideFault() {
    byID("fault").hidden = true;
  }

  // ------------------------------------------------------------- the snapshot

  function members() {
    return (state.snapshot && state.snapshot.members) || [];
  }

  function flow() {
    const value = (state.snapshot && state.snapshot.flow) || {};
    return {
      waits_feature: value.waits_feature || "unknown",
      deliveries: value.deliveries || [],
      waits: value.waits || [],
      by_member: value.by_member || {},
      deliveries_truncated: value.deliveries_truncated,
      waits_truncated: value.waits_truncated,
    };
  }

  function sectionError(name) {
    const errors = (state.snapshot && state.snapshot.section_errors) || {};
    return errors[name] || "";
  }

  function operator() {
    return (state.snapshot && state.snapshot.operator_principal) || (state.hello && state.hello.operator_principal) || "";
  }

  function labelOf(principal) {
    if (!principal) return "unknown";
    if (principal === operator()) return "you (operator)";
    for (const member of members()) {
      if (member.member === principal) return member.label || shortPrincipal(principal);
    }
    for (const handoff of (state.snapshot && state.snapshot.lineage) || []) {
      if (handoff.from_principal === principal && handoff.from_label) return handoff.from_label;
      if (handoff.to_principal === principal && handoff.to_label) return handoff.to_label;
    }
    if (state.landingLabels && state.landingLabels.has(principal)) return state.landingLabels.get(principal);
    return shortPrincipal(principal);
  }

  function shortPrincipal(principal) {
    const match = /^agent\/([0-9a-f]{8})-[0-9a-f-]{27}$/i.exec(principal);
    return match ? `agent/${match[1]}…` : principal;
  }

  // An agent destination names a principal: agent/UUID, or the operator.
  function destinationPrincipal(to) {
    if (!to || to.type !== "agent") return "";
    if (to.name.startsWith("agent/") || to.name === operator() || to.name.includes(":")) return to.name;
    return "agent/" + to.name;
  }

  function destinationLabel(to) {
    if (!to) return "unknown";
    if (to.type === "agent") return labelOf(destinationPrincipal(to));
    return `${to.type}/${to.name}`;
  }

  function memberByPrincipal(principal) {
    return members().find((member) => member.member === principal);
  }

  function deliveriesOf(eventID) {
    return flow().deliveries.filter((delivery) => delivery.event_id === eventID);
  }

  function renderCampaign() {
    if (!state.snapshot) return;
    renderHeader();
    renderNeeds(byID("needs"), state.snapshot.needs_you, sectionError("needs_you"), operator());
    renderAgents();
    renderNearLimit();
    renderFlow();
    if (byID("detail").open && state.detailMember) renderDetail(state.detailMember);
  }

  function renderHeader() {
    const snapshot = state.snapshot;
    const campaign = snapshot.campaign || {};
    byID("title").textContent = campaign.campaign || state.campaign;
    const description = byID("description");
    description.replaceChildren();
    if (campaign.description) {
      description.append(linked(campaign.description));
      description.hidden = false;
    } else {
      description.hidden = true;
    }
    const enrolled = members().filter((member) => member.active);
    const presence = { online: 0, offline: 0, stopped: 0 };
    for (const member of enrolled) {
      const p = member.session ? member.session.presence : "";
      if (p in presence) presence[p] += 1;
    }
    const states = Object.entries(presence).filter(([, n]) => n > 0).map(([p, n]) => `${n} ${p}`);
    const left = members().length - enrolled.length;
    const meta = byID("campaign-meta");
    meta.hidden = false;
    meta.replaceChildren(
      `${enrolled.length} member${enrolled.length === 1 ? "" : "s"} enrolled${states.length ? " (" + states.join(", ") + ")" : ""}`,
      left ? ` · ${left} no longer enrolled` : "", " · started ",
      timeEl(campaign.created_at),
      campaign.active === false ? " · campaign inactive" : "",
      snapshot.collection ? ` · collection ${snapshot.collection}` : "");
    renderObservations();
  }

  // Each observation has its own age; one stale observation does not hide the others.
  function renderObservations() {
    if (!state.campaign) return;
    const list = byID("observations");
    list.hidden = false;
    list.replaceChildren();
    const tickMS = (state.hello && state.hello.tick_ms) || 2000;
    const dbTime = parseTime(state.dbObservedAt);
    const dbStale = !!state.fault || dbTime === null || Date.now() - dbTime > tickMS * 4;
    list.append(observation("Database", dbTime, dbStale, state.fault ? "store unreadable" : ""));

    const panes = state.paneTick || state.herdr;
    if (!panes || panes.status === "pending") list.append(observation("Herdr", null, false, "first read pending"));
    else {
      const unavailable = panes.status === "unavailable";
      list.append(observation("Herdr", parseTime(panes.observed_at), !!panes.stale || unavailable,
        unavailable ? "unavailable" + (panes.error ? " (" + panes.error + ")" : "") : ""));
    }

    const transcripts = state.windowTick || state.window;
    if (!transcripts || transcripts.status === "pending") list.append(observation("Transcripts", null, false, "first read pending"));
    else {
      const unavailable = transcripts.status === "unavailable";
      list.append(observation("Transcripts", parseTime(transcripts.observed_at), !!transcripts.stale || unavailable,
        unavailable ? "unavailable" + (transcripts.error ? " (" + transcripts.error + ")" : "") : ""));
    }

    const room = state.snapshot && state.snapshot.context_room;
    const counters = room ? parseTime(room.observed_at) : null;
    if (sectionError("context_room")) list.append(observation("Session counters", null, true, "unavailable: " + sectionError("context_room")));
    else list.append(observation("Session counters", counters, false, counters === null ? "no counters recorded" : ""));
  }

  function observation(name, time, stale, detail) {
    const unavailable = detail.startsWith("unavailable");
    const item = el("li", { class: stale ? "stale" : "" }, el("strong", { text: name + ": " }));
    if (time !== null) item.append(timeEl(time));
    if (stale && !unavailable) item.append(el("span", { class: "flag", text: " stale" }));
    if (unavailable) item.append(el("span", { class: "flag", text: " unavailable" }));
    const rest = unavailable ? detail.replace(/^unavailable:?\s*/, "") : detail;
    if (rest) item.append(el("span", { class: "muted", text: (time !== null || unavailable ? " · " : "") + rest }));
    return item;
  }

  // ---------------------------------------------------------------- needs you

  function renderNeeds(container, needs, unavailableReason, operatorPrincipal) {
    container.replaceChildren();
    const heading = container.closest("section").querySelector("h2");
    if (unavailableReason) {
      heading.textContent = "Needs you (unavailable)";
      container.append(note("Needs you is unavailable: " + unavailableReason, "unavailable"));
      return;
    }
    const items = (needs && needs.items) || [];
    const unavailable = (needs && needs.unavailable) || [];
    heading.textContent = items.length ? `Needs you (${items.length})` : "Needs you";
    if (unavailable.length) {
      const list = el("ul", { class: "unavailable-sources", "aria-label": "Sources that could not be read" });
      for (const source of unavailable) list.append(el("li", { class: "unavailable", text: `${needLabel(source.type)}: unavailable (${source.reason})` }));
      container.append(list);
    }
    if (!items.length) {
      container.append(note(unavailable.length ? "Nothing else waits on you." : "Nothing waits on you."));
      return;
    }
    const list = el("ol", { class: "needs" });
    for (const need of items) list.append(needRow(need, operatorPrincipal));
    container.append(list);
  }

  const needLabels = {
    board_question: "Question for you", operator_delivery: "Sent to you", decision: "Decision",
    escalation: "Escalation", workstream_decision: "Workstream decision", handoff_due: "Handoff due",
    owner_turn: "Your turn", failed_delivery: "Failed delivery", stalled_agent: "Stalled agent",
    stopped_backlog: "Work sent to a stopped session", wait_timed_out: "Wait timed out",
    missed_checkpoint: "Missed checkpoint",
  };

  function needLabel(type) {
    return needLabels[type] || type;
  }

  // The request behind a row, when its event is in the window and the title
  // does not already name it.
  function needEventLine(need) {
    const refs = need.refs || {};
    const event = refs.event_id ? state.log.get(refs.event_id) : null;
    if (!event || need.type === "board_question" || need.type === "operator_delivery") return null;
    // The sender is always named; the summary only when the title lacks it.
    const named = event.summary && (need.title || "").includes(event.summary);
    return el("p", { class: "detail" }, `${event.kind} from ${senderLabel(event)} to ${destinationLabel(event.to)}`,
      named ? "" : [": ", event.summary ? linked(event.summary) : el("span", { class: "muted", text: "no summary" })]);
  }

  // Send goes only to current principals of an active campaign: the root,
  // members whose membership is active, and the campaign's role addresses.
  function canWrite(principal) {
    if (!state.snapshot || !principal) return false;
    const campaign = state.snapshot.campaign || {};
    if (campaign.active === false) return false;
    if (principal === campaign.root_agent || principal === campaign.role_address) return true;
    return members().some((m) => (m.member === principal && m.active) || m.role_address === principal);
  }

  const followUpTypes = new Set(["handoff_due", "missed_checkpoint", "wait_timed_out"]);

  // Parts joined by " · ", with no separator for a part that is absent.
  function joinParts(parts) {
    const present = parts.filter((part) => part !== null && part !== undefined && part !== "");
    return present.flatMap((part, i) => (i ? [" · ", part] : [part]));
  }

  function needRow(need, operatorPrincipal) {
    const action = need.action || {};
    const refs = need.refs || {};
    const row = el("li", { class: "need", dataset: { need: need.id || "", type: need.type || "", event: refs.event_id || "" } },
      el("div", { class: "need-head" },
        el("span", { class: "tag", text: needLabel(need.type) }),
        el("strong", {}, linked(need.title || "")),
        need.count > 1 ? el("span", { class: "count", text: ` ×${need.count}` }) : null),
      need.detail ? el("p", { class: "detail" }, linked(need.detail)) : null,
      needEventLine(need),
      el("p", { class: "meta" }, joinParts([
        need.subject ? labelOf(need.subject) : null,
        need.since ? ["since ", timeEl(need.since)] : null,
        need.expires_at ? ["expires ", timeEl(need.expires_at)] : null,
        need.waiters ? `${need.waiters} session${need.waiters === 1 ? "" : "s"} waiting on it` : null])));
    // A board question is answered on the board; the page says only that.
    const actionText = action.kind === "answer_board" ? "Answer on the board" : action.label || action.kind || "inspect";
    const controls = el("div", { class: "need-action" }, el("span", { class: "action-label", text: "Action: " + actionText }));
    const delivery = (refs.delivery_ids || [])[0];
    if (action.kind === "acknowledge" && delivery) {
      // The store acknowledges the operator's oldest delivery only. Until the
      // row says it is the inbox head, it is not.
      if (need.inbox_head === true) {
        controls.append(el("button", { type: "button", text: "Acknowledge", onclick: () => acknowledge(delivery, refs.event_id) }));
      } else {
        controls.append(el("span", { class: "muted not-head", text: "Not the next delivery in your inbox" }));
      }
    } else if (action.kind === "cancel_delivery" && delivery && state.campaign) {
      controls.append(el("button", { type: "button", text: "Cancel this recipient's delivery", onclick: () => cancelDelivery(delivery) }));
    } else if (action.kind === "cancel_wait" && refs.wait_id && state.campaign) {
      controls.append(el("button", { type: "button", text: "Cancel the wait", onclick: () => cancelWait(refs.wait_id) }));
    } else if (action.kind === "answer_board") {
      // A message without causation is not an answer: the board is where it
      // is answered, so the page offers no compose control here.
    } else if (action.kind === "reply" && refs.event_id && state.campaign) {
      const to = need.subject || (state.log.get(refs.event_id) || {}).from;
      if (to && canWrite(to)) controls.append(el("button", { type: "button", text: `Send a notice to ${labelOf(to)}`, onclick: () => replyTo(to, refs.event_id, need.title) }));
    } else if (action.kind === "decide" && need.subject && state.campaign && canWrite(need.subject)) {
      controls.append(el("button", { type: "button", text: `Write to ${labelOf(need.subject)}`, onclick: () => openDetail(need.subject, true) }));
    } else if (followUpTypes.has(need.type) && state.campaign) {
      // "Follow up with X": the row names X in refs.principal (or as its subject).
      const target = refs.principal || need.subject;
      if (target && canWrite(target)) controls.append(el("button", { type: "button", text: `Write to ${labelOf(target)}`, onclick: () => openDetail(target, true) }));
    }
    if (refs.event_id && state.campaign) controls.append(el("button", { type: "button", class: "secondary", text: "Open the event", onclick: () => openEvent(refs.event_id) }));
    if (action.command) controls.append(copyButton(action.command, "Copy command"));
    row.append(controls);
    return row;
  }

  // ------------------------------------------------------------------- agents

  // At desktop width the agents are a table, one row per member, so that the
  // owner can read down a column; below it each member is a card.
  const wideAgents = window.matchMedia("(min-width: 1100px)");
  wideAgents.addEventListener("change", () => renderAgents());

  const roleOrder = { root: 0, lead: 1, worker: 2 };
  const presenceOrder = { online: 0, offline: 1, stopped: 2 };
  const assignmentOrder = { in_progress: 0, review: 1, dispatched: 2, planned: 3 };

  // Root first, then leads, then workers; within a group online before
  // offline before stopped, then by label.
  function memberOrder(a, b) {
    const role = (roleOrder[a.role] ?? 3) - (roleOrder[b.role] ?? 3);
    if (role) return role;
    const presence = (m) => (m.session ? presenceOrder[m.session.presence] ?? 3 : 4);
    const live = presence(a) - presence(b);
    if (live) return live;
    return String(a.label || a.member).localeCompare(String(b.label || b.member), undefined, { numeric: true });
  }

  function renderAgents() {
    const container = byID("agents");
    if (!state.snapshot) return;
    container.replaceChildren();
    if (sectionError("members")) {
      container.append(note("The roster is unavailable: " + sectionError("members"), "unavailable"));
      return;
    }
    const lineage = state.snapshot.lineage || [];
    const handedOff = new Set(lineage.map((handoff) => handoff.from_principal));
    const holders = new Set(members().filter((m) => m.role_address).map((m) => m.member));
    const due = state.snapshot.handoff;
    const dueBox = byID("handoff-due");
    dueBox.replaceChildren();
    if (due) dueBox.append(note(`Coordinator handoff due for ${labelOf(due.holder_principal)}: ${(due.reasons || []).join("; ")}`, "warning"));

    const feature = flow().waits_feature;
    if (!sectionError("flow") && feature !== "active") {
      dueBox.append(note(`Waits feature ${feature}: agents cannot register waits, so "Waits on" and peer waits stay empty.`));
    }
    // The near-limit rule is stated once, above the Context room column.
    if (nearLimitRule()) dueBox.append(el("p", { class: "rule context-rule" }, el("strong", { text: "Context room: " }), nearLimitRule()));
    const ordered = members().slice().sort(memberOrder);
    const active = ordered.filter((member) => member.active || holders.has(member.member));
    const inactive = ordered.filter((member) => !member.active && !holders.has(member.member) && !handedOff.has(member.member));
    if (!active.length) container.append(note("This campaign has no active member."));
    else container.append(agentLayout(active));
    if (inactive.length) {
      container.append(el("details", { class: "inactive-members" },
        el("summary", { text: `Inactive members (${inactive.length})` }), agentLayout(inactive)));
    }

    const lineageBox = byID("lineage");
    lineageBox.replaceChildren();
    if (lineage.length) {
      const list = el("ol", { class: "lineage" });
      for (const handoff of lineage) {
        list.append(el("li", {},
          `${handoff.from_label || shortPrincipal(handoff.from_principal)} → ${handoff.to_label || shortPrincipal(handoff.to_principal)}`,
          ` · ${handoff.role_address} · ${handoff.reason || "no reason recorded"} · `, timeEl(handoff.created_at)));
      }
      lineageBox.append(el("details", {}, el("summary", { text: `Coordinator lineage (${lineage.length} handoff${lineage.length === 1 ? "" : "s"})` }), list));
    }
  }

  // Each column of the table and each field of a card is one of these.
  const agentColumns = [
    { name: "Agent", cell: agentCell },
    { name: "State", cell: stateCell },
    { name: "Working on", cell: workingCell },
    { name: "Holds", cell: holdsCell },
    { name: "Waiting", cell: waitingCell },
    { name: "Context room", cell: contextView },
    { name: "Pane", cell: paneView },
  ];

  function agentLayout(list) {
    if (wideAgents.matches) {
      const table = el("table", { class: "agents-table" },
        el("caption", { class: "visually-hidden", text: "Campaign members" }),
        el("thead", {}, el("tr", {}, agentColumns.map((column) => el("th", { scope: "col", text: column.name })))));
      const body = el("tbody");
      for (const member of list) {
        const selected = state.filter === member.member;
        const row = el("tr", { class: `agent${member.active ? "" : " inactive"}${selected ? " selected" : ""}${nearLimit(member) ? " near-limit-row" : ""}`,
          dataset: { member: member.member, label: member.label || shortPrincipal(member.member) } },
          agentColumns.map((column, i) => el(i === 0 ? "th" : "td", i === 0 ? { scope: "row" } : {}, column.cell(member))));
        // A click anywhere on the row opens the detail panel; the controls in
        // the first cell keep their own meaning and are the keyboard path.
        row.addEventListener("click", (event) => {
          if (event.target.closest("button, a, summary, input, select, textarea, details[open]")) return;
          // Selecting text is not a request to open the row.
          if (String(window.getSelection() || "").trim()) return;
          openDetail(member.member);
        });
        body.append(row);
      }
      table.append(body);
      return el("div", { class: "table-wrap" }, table);
    }
    const cards = el("div", { class: "agents-cards" });
    for (const member of list) cards.append(agentCard(member));
    return cards;
  }

  function agentCard(member) {
    const label = member.label || shortPrincipal(member.member);
    const selected = state.filter === member.member;
    const card = el("article", { class: `agent${member.active ? "" : " inactive"}${selected ? " selected" : ""}${nearLimit(member) ? " near-limit-row" : ""}`, dataset: { member: member.member, label }, "aria-label": label });
    card.append(el("header", { class: "agent-head" }, agentCell(member)));
    const fields = el("dl", { class: "fields" });
    for (const column of agentColumns.slice(1)) fields.append(field(column.name, column.cell(member)));
    card.append(fields);
    return card;
  }

  function field(name, ...value) {
    return el("div", { class: "field" }, el("dt", { text: name }), el("dd", {}, ...value));
  }

  function agentCell(member) {
    const label = member.label || shortPrincipal(member.member);
    const selected = state.filter === member.member;
    return el("div", { class: "agent-cell" },
      el("div", { class: "agent-name" },
        el("strong", { class: "agent-label", text: label }), " ",
        el("span", { class: "role", text: member.role_label || member.role || "member" }),
        member.role_label && member.role ? el("span", { class: "muted", text: ` (${member.role})` }) : null),
      member.role_address ? el("code", { class: "role-address", text: member.role_address }) : null,
      member.active ? null : el("span", { class: "tag", text: "inactive member" }),
      nearLimit(member) ? el("span", { class: "tag flag near-limit-tag", text: "⚠ near context limit" }) : null,
      el("div", { class: "agent-actions" },
        el("button", { type: "button", class: "small", text: "Details", "aria-label": `Details and actions for ${label}`, onclick: () => openDetail(member.member) }),
        el("button", { type: "button", class: "small secondary", "aria-pressed": selected ? "true" : "false",
          text: selected ? "All messages" : "Messages", "aria-label": selected ? "Show all messages" : `Show the messages of ${label}`,
          onclick: () => setFilter(selected ? "" : member.member) })));
  }

  function stateCell(member) {
    const session = member.session;
    if (!session) return el("span", { class: "muted", text: "no registered session" });
    return el("div", {}, presenceText(session),
      el("div", { class: "small-text muted", text: [session.harness, session.observed_model || session.model].filter(Boolean).join(" · ") || "harness not reported" }),
      el("div", { class: "small-text muted session-age" }, "session started ", timeEl(session.started_at)));
  }

  // What the member itself holds first: its open assignments. The reported
  // task summary follows, unless it is the derived campaign line, which says
  // nothing about this agent.
  // The members below a coordinator: its reports, their reports, and so on.
  function subtreeOf(member) {
    const below = [];
    const seen = new Set([member.member]);
    let frontier = [member.member];
    while (frontier.length) {
      const next = [];
      for (const m of members()) {
        if (m.active && m.lead && frontier.includes(m.lead) && !seen.has(m.member)) {
          seen.add(m.member);
          below.push(m);
          next.push(m.member);
        }
      }
      frontier = next;
    }
    return below;
  }

  // Who is working on what, without opening anything. In this order, each
  // labelled by its source: the member's open ledger assignments; for a
  // coordinator, what it coordinates; the session's own task text, also when
  // it is the summary that the ledger sync writes.
  function workingCell(member) {
    const parts = [];
    const assignments = (member.holds || []).filter((hold) => hold.kind === "assignment")
      .sort((a, b) => (assignmentOrder[a.status] ?? 4) - (assignmentOrder[b.status] ?? 4));
    if (assignments.length) {
      parts.push(el("div", { class: "source" }, el("span", { class: "source-label", text: "Ledger assignments" }),
        el("ul", { class: "plain working" }, assignments.map((hold) => el("li", {},
          el("span", { class: `tag status-${hold.status}`, text: hold.status.replace("_", " ") }), " ",
          linked(hold.summary || hold.item_key),
          (hold.plane_issues || []).filter((id) => !(hold.summary || "").includes(id)).map((id) => [" ", planeLink(id)]),
          hold.due_at ? [" · due ", timeEl(hold.due_at)] : null)))));
    }
    const coordinator = member.role === "root" || member.role === "lead" ||
      (state.snapshot && state.snapshot.campaign && state.snapshot.campaign.root_agent === member.member);
    if (coordinator) {
      const below = member.role === "root" || member.role_address ? members().filter((m) => m.active && m.member !== member.member) : subtreeOf(member);
      const principals = new Set(below.map((m) => m.member));
      const open = ((state.snapshot && state.snapshot.ledger) || []).filter((item) => item.open && principals.has(item.owner));
      const count = (kind) => open.filter((item) => item.kind === kind).length;
      const reports = members().filter((m) => m.active && m.lead === member.member);
      if (below.length || open.length) {
        const counts = [["assignment", "assignments"], ["blocker", "blockers"], ["decision", "decisions"], ["hold", "holds"]]
          .map(([kind, plural]) => [count(kind), kind, plural]).filter(([n]) => n > 0)
          .map(([n, kind, plural]) => `${n} ${n === 1 ? kind : plural}`);
        parts.push(el("div", { class: "source" }, el("span", { class: "source-label", text: "Coordinates" }),
          el("p", {}, reports.length ? `${reports.map((m) => m.label || shortPrincipal(m.member)).join(", ")}` : `${below.length} member${below.length === 1 ? "" : "s"}`,
            counts.length ? ` · open: ${counts.join(", ")}` : " · nothing open in the ledger")));
      }
    }
    const task = member.session && member.session.task_summary;
    if (task) {
      parts.push(el("div", { class: "source" }, el("span", { class: "source-label", text: "Reported by the session" }), longText(task, 140)));
    }
    if (!parts.length) parts.push(el("span", { class: "muted", text: "no assignment recorded" }));
    return parts;
  }

  // Open ledger items other than assignments, leased deliveries, and on a
  // lead's row the decisions that lead owns.
  function holdsCell(member) {
    const memberFlow = flow().by_member[member.member];
    const holds = (member.holds || []).filter((hold) => hold.kind !== "assignment");
    const parts = [];
    if (holds.length) {
      parts.push(el("ul", { class: "plain" }, holds.map((hold) => el("li", {},
        el("span", { class: "tag", text: hold.kind }), " ", el("strong", { text: hold.item_key }), ` (${hold.status}) `, linked(hold.summary)))));
    }
    if (memberFlow && (memberFlow.leased || memberFlow.held)) {
      parts.push(el("p", { text: `${memberFlow.leased || 0} leased deliver${memberFlow.leased === 1 ? "y" : "ies"}${memberFlow.held ? `, ${memberFlow.held} held` : ""}` }));
    }
    const decisions = ((state.snapshot.needs_you && state.snapshot.needs_you.lead_decisions) || {})[member.member] || [];
    if (decisions.length) {
      parts.push(el("div", { class: "decisions" }, el("strong", { text: "Decisions to make" }),
        el("ul", { class: "plain" }, decisions.map((d) => el("li", {}, el("strong", { text: d.item_key + ": " }), linked(d.summary), " · ", timeEl(d.updated_at))))));
    }
    if (!parts.length) parts.push(el("span", { class: "muted", text: "nothing open" }));
    return parts;
  }

  // Waits on, waited on by a peer, and the oldest pending item stay separate facts.
  function waitingCell(member) {
    const memberFlow = flow().by_member[member.member];
    return el("dl", { class: "facts" },
      el("dt", { text: "Waits on" }), el("dd", {}, waitsOnView(member, memberFlow)),
      el("dt", { text: "Oldest pending" }), el("dd", {}, oldestPendingView(memberFlow)),
      el("dt", { text: "Waited on" }), el("dd", {}, waitedOnView(member, memberFlow)));
  }

  function presenceText(session) {
    const parts = [session.presence || "unknown"];
    if (session.state) parts.push(session.state);
    const text = parts.join(", ");
    if (session.presence !== "online" && session.last_seen) return el("span", { class: `presence presence-${session.presence || "unknown"}` }, text + " · last seen ", timeEl(session.last_seen));
    return el("span", { class: `presence presence-${session.presence || "unknown"}`, text });
  }

  function waitByID(id) {
    return flow().waits.find((wait) => wait.wait_id === id);
  }

  function waitText(wait) {
    if (!wait) return el("span", { class: "muted", text: "wait not in this snapshot" });
    const kind = wait.predicate === "response_group_closed" ? "group closure" : "direct reply";
    return el("span", {},
      el("span", { class: "tag", text: kind }), " ", linked(wait.dependency_summary || wait.dependency_event_id),
      wait.awaited && wait.awaited.length ? ` · from ${wait.awaited.map(labelOf).join(", ")}` : "",
      wait.overdue ? el("span", { class: "flag", text: " overdue" }) : null,
      " · deadline ", timeEl(wait.deadline));
  }

  function waitsOnView(member, memberFlow) {
    if (sectionError("flow")) return el("span", { class: "unavailable", text: "unavailable" });
    const ids = (memberFlow && memberFlow.waits_on) || [];
    if (!ids.length) return el("span", { class: "muted", text: "no registered wait" });
    return el("ul", { class: "plain" }, ids.map((id) => el("li", {}, waitText(waitByID(id)))));
  }

  // The oldest item in its inbox. Queue age alone does not mean a peer waits.
  function oldestPendingView(memberFlow) {
    if (sectionError("flow")) return el("span", { class: "unavailable", text: "unavailable" });
    if (!memberFlow || !memberFlow.oldest_pending) return el("span", { class: "muted", text: "inbox empty" });
    const delivery = flow().deliveries.find((d) => d.delivery_id === memberFlow.oldest_pending);
    return el("span", {},
      delivery ? [linked(delivery.summary || delivery.kind), ` from ${senderLabel(delivery)}, `, timeEl(delivery.created_at)] : memberFlow.oldest_pending,
      memberFlow.pending > 1 ? ` (${memberFlow.pending} pending)` : "");
  }

  // A registered wait of a peer on this member's reply.
  function waitedOnView(member, memberFlow) {
    if (sectionError("flow")) return el("span", { class: "unavailable", text: "unavailable" });
    const peers = (memberFlow && memberFlow.peer_waits) || [];
    if (!peers.length) return el("span", { class: "muted", text: "no peer waiting" });
    return el("ul", { class: "plain" }, peers.map((id) => {
      const wait = waitByID(id);
      return el("li", {}, wait ? `${labelOf(wait.owner)} waits for ` : "", waitText(wait));
    }));
  }

  // Token counts as the owner reads them: 807k, 1M, 1.2M.
  function tokens(value) {
    const n = Number(value) || 0;
    if (n >= 1e6) return `${Number((n / 1e6).toFixed(n % 1e6 === 0 ? 0 : 1))}M`;
    if (n >= 1e3) return `${Math.round(n / 1e3)}k`;
    return String(n);
  }

  // The true fill read from the harness transcript: "81% · 807k of 1M tokens",
  // or tokens only when the window size is not known.
  function formatWindow(window) {
    const used = Number(window && window.used_tokens) || 0;
    const size = Number(window && window.window_tokens) || 0;
    if (size > 0) return `${Math.round((used / size) * 100)}% · ${tokens(used)} of ${tokens(size)} tokens`;
    return `${tokens(used)} tokens`;
  }

  function windowOf(member) {
    const observed = state.window;
    if (!observed || !member.agent_id) return null;
    return (observed.windows || {})[member.agent_id] || null;
  }

  function proxyOf(member) {
    const room = (state.snapshot && state.snapshot.context_room && state.snapshot.context_room.by_agent) || {};
    return member.agent_id ? room[member.agent_id] || null : null;
  }

  // Near the limit by the transcript's rule, or by the Cairn proxy's rule.
  function nearLimit(member) {
    const window = windowOf(member);
    if (window && window.near_limit) return true;
    const proxy = proxyOf(member);
    return !!(proxy && proxy.known && proxy.near_limit);
  }

  function renderNearLimit() {
    const box = byID("near-limit-count");
    if (!state.campaign || !state.snapshot) {
      box.hidden = true;
      return;
    }
    const near = members().filter((member) => (member.active || member.role_address) && nearLimit(member));
    box.hidden = !near.length;
    box.textContent = near.length
      ? `⚠ ${near.length} agent${near.length === 1 ? "" : "s"} near ${near.length === 1 ? "its" : "their"} context limit: ${near.map((m) => m.label || shortPrincipal(m.member)).join(", ")}`
      : "";
  }

  // Both rules: the transcript fill's and the Cairn proxy's.
  function nearLimitRule() {
    const fill = (state.window && state.window.near_limit_rule) || "";
    const proxy = (state.snapshot && state.snapshot.context_room && state.snapshot.context_room.near_limit_rule) || "";
    return [fill && "Transcript fill: " + fill, proxy && "Cairn-injected proxy: " + proxy].filter(Boolean).join(" ");
  }

  function contextView(member) {
    const parts = [];
    const window = windowOf(member);
    const observed = state.window;
    if (window) {
      if (window.near_limit) parts.push(el("p", { class: "near-limit" }, el("strong", { text: "⚠ near its limit" })));
      parts.push(el("p", { class: "window-fill" }, el("strong", { text: formatWindow(window) })));
      const detail = [];
      detail.push(`${window.compactions} compaction${window.compactions === 1 ? "" : "s"}`);
      parts.push(el("p", { class: "small-text muted" }, detail.join(""),
        window.last_compaction_at ? [", last ", timeEl(window.last_compaction_at)] : "",
        " · measured ", timeEl(window.measured_at), window.source ? ` · ${window.source}` : ""));
    } else if (observed && member.agent_id && (observed.unknown || {})[member.agent_id]) {
      parts.push(el("p", {}, el("strong", { text: "unknown" }), el("span", { class: "muted", text: `: ${observed.unknown[member.agent_id]}` })));
    } else if (!observed || observed.status === "pending") {
      parts.push(el("p", { class: "muted", text: "reading transcripts" }));
    } else if (observed.status === "unavailable") {
      parts.push(el("p", { class: "unavailable", title: observed.error || "", text: "unknown: transcripts unavailable" }));
    } else {
      parts.push(el("p", { class: "muted", text: "unknown: no transcript on this host" }));
    }

    // The Cairn proxy stays under the true figure, labelled as a proxy.
    if (sectionError("context_room")) {
      parts.push(el("p", { class: "small-text unavailable", text: "Cairn-injected since compaction: unavailable" }));
      return parts;
    }
    const proxy = proxyOf(member);
    if (proxy && (proxy.compactions !== undefined && proxy.compactions !== null || proxy.last_compaction_at) && !window) {
      parts.push(el("p", { class: "small-text muted" },
        proxy.compactions !== undefined && proxy.compactions !== null ? `${proxy.compactions} compaction${proxy.compactions === 1 ? "" : "s"} recorded` : "compactions unknown",
        proxy.last_compaction_at ? [", last ", timeEl(proxy.last_compaction_at)] : ""));
    }
    if (proxy && proxy.known) {
      const counts = [];
      if (proxy.injected_deliveries !== undefined && proxy.injected_deliveries !== null) counts.push(`${proxy.injected_deliveries} deliveries`);
      if (proxy.injected_recalls !== undefined && proxy.injected_recalls !== null) counts.push(`${proxy.injected_recalls} recalls`);
      parts.push(el("p", { class: "small-text proxy" }, "Cairn-injected since compaction (proxy): ",
        proxy.injected_bytes !== undefined && proxy.injected_bytes !== null ? bytes(proxy.injected_bytes) : "unknown",
        counts.length ? ` (${counts.join(", ")})` : "",
        proxy.near_limit ? el("strong", { class: "near-limit", text: " ⚠ near limit" + (proxy.reasons && proxy.reasons.length ? ": " + proxy.reasons.join("; ") : "") }) : null));
    } else {
      parts.push(el("p", { class: "small-text muted", text: "Cairn-injected since compaction (proxy): unknown" }));
    }
    return parts;
  }

  function paneView(member) {
    const panes = state.herdr;
    if (!panes || panes.status === "pending") return el("span", { class: "muted", text: "reading Herdr" });
    const pane = member.agent_id && panes.panes ? panes.panes[member.agent_id] : null;
    if (pane) {
      return el("span", {},
        el("span", { class: "tag", text: pane.agent_status || "unknown" }), ` ${pane.pane_id}`,
        pane.terminal_title ? [" · ", el("span", { class: "text", text: pane.terminal_title })] : null,
        panes.stale ? el("span", { class: "flag", text: " stale" }) : null,
        el("br"), copyButton(pane.focus_command || `herdr agent focus ${pane.pane_id}`, "Copy focus command"));
    }
    // The reason is in the header, once.
    if (panes.status === "unavailable") return el("span", { class: "unavailable", title: panes.error || "", text: "Herdr unavailable" });
    return el("span", { class: "muted", text: "no pane on this host" });
  }

  function setFilter(principal) {
    state.filter = principal;
    renderAgents();
    renderFlow();
  }

  // ---------------------------------------------------------------- the flow

  function eventsNewestFirst() {
    return [...state.log.values()].sort((a, b) => b.position - a.position);
  }

  function involves(event, principal) {
    if (event.from === principal) return true;
    if (destinationPrincipal(event.to) === principal) return true;
    return deliveriesOf(event.event_id).some((delivery) => delivery.consumer === principal);
  }

  function renderFlow() {
    const list = byID("flow");
    list.replaceChildren();
    const filterNote = byID("flow-filter");
    if (state.filter) {
      filterNote.hidden = false;
      filterNote.replaceChildren(`Showing messages of ${labelOf(state.filter)}. `,
        el("button", { type: "button", text: "Show all", onclick: () => setFilter("") }));
    } else {
      filterNote.hidden = true;
    }
    const currentFlow = flow();
    const meta = byID("flow-meta");
    const shownCount = state.filter ? eventsNewestFirst().filter((event) => involves(event, state.filter)).length : state.log.size;
    const metaParts = [state.filter
      ? `${shownCount} of ${state.log.size} events in the window, filtered to ${labelOf(state.filter)}`
      : `${state.log.size} event${state.log.size === 1 ? "" : "s"} in the window (newest ${state.logWindow} kept)`];
    if (sectionError("flow")) metaParts.push("delivery states unavailable: " + sectionError("flow"));
    else metaParts.push(`waits feature ${currentFlow.waits_feature}`);
    if (currentFlow.deliveries_truncated) metaParts.push("delivery list truncated");
    meta.textContent = metaParts.join(" · ");

    let events = eventsNewestFirst();
    if (state.filter) events = events.filter((event) => involves(event, state.filter));
    if (!events.length) {
      list.append(el("li", { class: "empty" }, state.filter ? "No message of this agent in the window." : "No message in this campaign yet."));
      return;
    }
    const ledgerByEvent = new Map();
    for (const item of (state.snapshot && state.snapshot.ledger) || []) {
      if (!item.event_id) continue;
      if (!ledgerByEvent.has(item.event_id)) ledgerByEvent.set(item.event_id, []);
      ledgerByEvent.get(item.event_id).push(item);
    }
    for (const event of events) list.append(flowRow(event, ledgerByEvent.get(event.event_id) || []));
  }

  // The page never says "settle": a settled delivery is one its recipient handled.
  const displayWords = { settled: "handled" };

  function deliveryChip(delivery, ownerSent) {
    const display = delivery.display || delivery.state || "unknown";
    const chip = el("span", { class: `chip chip-${display}`, dataset: { delivery: delivery.delivery_id, display } },
      `${labelOf(delivery.consumer)}: `, el("span", { "aria-hidden": "true", text: (displayMarks[display] || "?") + " " }), displayWords[display] || display);
    if (delivery.code && delivery.code !== display) chip.append(` (${delivery.code})`);
    if (ownerSent && !delivery.seen && openDisplays.has(display)) chip.append(el("span", { class: "unseen", text: " · sent, not yet seen" }));
    if (delivery.replied) chip.append(" · replied");
    return chip;
  }

  const systemWords = {
    wait_notice: "wait notice", stopped_delivery_notice: "stopped-session notice", overdue_checkpoint: "overdue checkpoint",
    scheduled_event: "scheduled message",
  };

  // Cairn itself publishes some events under the operator principal (the
  // scheduler's wait and checkpoint notices). Such an event, and each of its
  // deliveries, carries `system`. A delivery without the value falls back to
  // its event's when the event is in the window. These three functions are
  // the only place that decides how a sender is shown and whether something
  // is the owner's own.
  function systemOf(item) {
    if (!item) return "";
    if (item.system) return item.system;
    if (item.delivery_id && item.event_id) {
      const event = state.log.get(item.event_id);
      if (event && event.system) return event.system;
    }
    return "";
  }

  function senderLabel(item) {
    const system = systemOf(item);
    if (system) return `Cairn (${systemWords[system] || String(system).replace(/_/g, " ")})`;
    return labelOf(item && item.from);
  }

  function fromOwner(item) {
    return !!item && item.from === operator() && !systemOf(item);
  }

  function flowRow(event, ledgerItems) {
    const deliveries = deliveriesOf(event.event_id);
    const ownerSent = fromOwner(event);
    const row = el("li", { class: `event event-${event.kind}${ownerSent ? " owner-sent" : ""}`, dataset: { event: event.event_id, position: String(event.position) } });
    row.append(el("div", { class: "event-head" },
      el("span", { class: "tag kind", text: event.kind }),
      event.action ? el("span", { class: "tag", text: "action " + event.action }) : null,
      event.blocker ? el("span", { class: "tag flag", text: "blocker" }) : null,
      event.grouped ? el("span", { class: "tag", text: "grouped" }) : null,
      el("span", { class: "route", text: `${senderLabel(event)} → ${destinationLabel(event.to)}` }),
      event.system ? el("span", { class: "tag system", text: "from Cairn" }) : null,
      el("span", { class: "muted" }, " · ", timeEl(event.created_at))));
    const cause = event.causation_id ? state.log.get(event.causation_id) : null;
    row.append(el("p", { class: "summary" }, event.summary ? linked(event.summary)
      : cause ? ["Reply to: ", linked(cause.summary || cause.kind)]
      : el("span", { class: "muted", text: event.kind === "response" ? "reply to a request outside the window" : "no summary" })));
    const states = el("div", { class: "deliveries", "aria-label": "Delivery state of each recipient" });
    if (deliveries.length) states.append(...deliveries.map((delivery) => deliveryChip(delivery, ownerSent)));
    else states.append(el("span", { class: "muted", text: sectionError("flow") ? "delivery state unavailable"
      : event.to && event.to.type === "topic" ? "no subscriber received it" : "no recipient recorded" }));
    row.append(states);
    if (event.supersedes) row.append(el("p", { class: "meta", text: "topic key " + event.supersedes }));
    if (ledgerItems.length) {
      row.append(el("p", { class: "ledger-link" }, "Ledger: ", ledgerItems.map((item, i) => [i ? ", " : "", `${item.kind} ${item.item_key} (${item.status})`])));
    }
    const actions = el("div", { class: "event-actions" }, el("button", { type: "button", class: "secondary", text: "Open", onclick: () => openEvent(event.event_id) }));
    const stillOpen = deliveries.some((delivery) => openDisplays.has(delivery.display || delivery.state));
    if (ownerSent && stillOpen && event.kind === "notice" && event.supersedes && event.to && event.to.type === "agent" && canWrite(destinationPrincipal(event.to))) {
      const target = destinationPrincipal(event.to);
      actions.append(el("button", { type: "button", text: "Supersede this notice", onclick: () => openDetail(target, true, event.supersedes) }));
    }
    row.append(actions);
    return row;
  }

  // ------------------------------------------------------------ agent detail

  function openDialog(id) {
    const dialog = byID(id);
    if (!dialog.open) dialog.showModal();
  }

  for (const button of document.querySelectorAll("[data-close]")) {
    button.addEventListener("click", () => byID(button.dataset.close).close());
  }
  byID("detail").addEventListener("close", () => { state.detailMember = ""; });
  byID("event-detail").addEventListener("close", () => { state.eventDetail = ""; });

  function openDetail(principal, focusCompose, supersedes) {
    state.detailMember = principal;
    renderDetail(principal);
    openDialog("detail");
    if (focusCompose) {
      const form = byID("detail-body").querySelector("form.compose");
      if (form) {
        if (supersedes) {
          form.elements.kind.value = "notice";
          form.elements.supersedes.value = supersedes;
          updateComposeKind(form);
        }
        form.elements.summary.focus();
      }
    }
  }

  function renderDetail(principal) {
    const member = memberByPrincipal(principal);
    const label = member ? member.label || shortPrincipal(principal) : labelOf(principal);
    byID("detail-title").textContent = label;
    const body = byID("detail-body");
    // Keep a draft that the owner is writing across snapshot updates.
    const existing = body.querySelector("form.compose");
    const draft = existing && existing.dataset.principal === principal ? existing : null;
    body.replaceChildren();
    body.append(el("p", { class: "meta" }, el("code", { text: principal }),
      member && member.session ? ` · ${presenceText(member.session).textContent}` : ""));

    const ledger = ((state.snapshot && state.snapshot.ledger) || []).filter((item) => item.owner === principal && item.open);
    body.append(el("h3", { text: "Ledger items held" }),
      ledger.length ? el("ul", { class: "plain" }, ledger.map((item) => el("li", {},
        el("span", { class: "tag", text: item.kind }), " ", el("strong", { text: item.item_key }), ` (${item.status}) `, linked(item.summary),
        item.due_at ? [" · due ", timeEl(item.due_at)] : null)))
        : note(sectionError("ledger") ? "unavailable: " + sectionError("ledger") : "none"));

    const deliveries = flow().deliveries;
    const incoming = deliveries.filter((d) => d.consumer === principal && openDisplays.has(d.display || d.state));
    const outgoing = deliveries.filter((d) => d.from === principal && openDisplays.has(d.display || d.state));
    body.append(el("h3", { text: "Open deliveries to it" }), deliveryList(incoming, true));
    body.append(el("h3", { text: "Open deliveries from it" }), deliveryList(outgoing, false));

    const waits = flow().waits.filter((w) => w.owner === principal || (w.awaited || []).includes(principal));
    body.append(el("h3", { text: "Waits" }), waits.length ? el("ul", { class: "plain" }, waits.map((wait) => el("li", {},
      `${labelOf(wait.owner)} (${wait.state}) `, waitText(wait),
      wait.state === "waiting" ? [" ", el("button", { type: "button", text: "Cancel the wait", onclick: () => cancelWait(wait.wait_id) })] : null)))
      : note(sectionError("flow") ? "unavailable: " + sectionError("flow") : `none (waits feature ${flow().waits_feature})`));

    const recent = eventsNewestFirst().filter((event) => involves(event, principal)).slice(0, 20);
    body.append(el("h3", { text: "Recent events" }), recent.length ? el("ul", { class: "plain" }, recent.map((event) => el("li", {},
      el("span", { class: "tag", text: event.kind }), ` ${senderLabel(event)} → ${destinationLabel(event.to)}: `, linked(event.summary || ""), " · ", timeEl(event.created_at), " ",
      el("button", { type: "button", class: "secondary", text: "Open", onclick: () => openEvent(event.event_id) }))))
      : note("none in the window"));

    body.append(el("h3", { text: "Write to " + label }));
    if (canWrite(principal) || (member && member.role_address && canWrite(member.role_address))) {
      body.append(draft || composeForm(principal, member && member.role_address));
    } else {
      body.append(note(state.snapshot && state.snapshot.campaign && state.snapshot.campaign.active === false
        ? "The campaign is inactive; the page sends to no one in it."
        : "Not a current principal of the campaign (it left, or handed off); the page sends only to the root, active members and role addresses."));
    }
  }

  function deliveryList(list, incoming) {
    if (sectionError("flow")) return note("unavailable: " + sectionError("flow"), "unavailable");
    if (!list.length) return note("none");
    return el("ul", { class: "plain" }, list.map((delivery) => el("li", {},
      deliveryChip(delivery, fromOwner(delivery)), " ",
      el("span", { class: "tag", text: delivery.kind }), " ",
      linked(delivery.summary || ""), ` · ${incoming ? "from " + senderLabel(delivery) : "to " + labelOf(delivery.consumer)} · `, timeEl(delivery.created_at), " ",
      deliveryControls(delivery))));
  }

  function deliveryControls(delivery) {
    const controls = [];
    if (delivery.consumer === operator() && openDisplays.has(delivery.display || delivery.state)) {
      if (isInboxHead(delivery.delivery_id)) {
        controls.push(el("button", { type: "button", text: "Acknowledge", onclick: () => acknowledge(delivery.delivery_id, delivery.event_id) }));
      } else {
        controls.push(el("span", { class: "muted not-head", text: "Not the next delivery in your inbox" }));
      }
    } else if (openDisplays.has(delivery.display || delivery.state)) {
      controls.push(el("button", { type: "button", text: "Cancel this recipient's delivery", onclick: () => cancelDelivery(delivery.delivery_id) }));
    }
    return controls;
  }

  // The needs-you row that the store marks as the operator's oldest eligible delivery.
  function isInboxHead(deliveryID) {
    const items = (state.snapshot && state.snapshot.needs_you && state.snapshot.needs_you.items) || [];
    return items.some((need) => need.inbox_head === true && ((need.refs || {}).delivery_ids || []).includes(deliveryID));
  }

  function updateComposeKind(form) {
    const notice = form.elements.kind.value === "notice";
    form.querySelector(".notice-only").hidden = !notice;
  }

  function composeForm(principal, roleAddress) {
    const form = el("form", { class: "compose", dataset: { principal } });
    const ids = { to: uuid(), kind: uuid(), action: uuid(), summary: uuid(), body: uuid(), supersedes: uuid() };
    // The holder of a role address can be written to by its address, which
    // follows the role across a handoff.
    const targets = [principal, roleAddress].filter((target) => target && canWrite(target));
    form.append(
      el("div", { class: "row" },
        el("label", { for: ids.to, text: "To" }),
        el("select", { id: ids.to, name: "to" }, targets.map((target) => el("option", { value: target,
          text: target === roleAddress ? `${target} (the role, whoever holds it)` : `${labelOf(target)} (${target})` })))),
      el("div", { class: "row" },
        el("label", { for: ids.kind, text: "Kind" }),
        el("select", { id: ids.kind, name: "kind" }, el("option", { value: "request", text: "request" }), el("option", { value: "notice", text: "notice" }))),
      el("div", { class: "notice-only", hidden: true },
        el("div", { class: "row" },
          el("label", { for: ids.action, text: "Action" }),
          el("select", { id: ids.action, name: "action" }, ["none", "review", "decide"].map((value) => el("option", { value, text: value })))),
        el("div", { class: "row" },
          el("label", { for: ids.supersedes, text: "Topic key (supersedes your earlier notice with this key)" }),
          el("input", { id: ids.supersedes, name: "supersedes", type: "text", maxlength: "200", autocomplete: "off" }))),
      el("div", { class: "row" },
        el("label", { for: ids.summary, text: "Summary" }),
        el("input", { id: ids.summary, name: "summary", type: "text", maxlength: "280", required: true, autocomplete: "off" })),
      el("div", { class: "row" },
        el("label", { for: ids.body, text: "Message" }),
        el("textarea", { id: ids.body, name: "body", rows: "5", required: true })),
      el("p", { class: "result", role: "status", "aria-live": "polite" }),
      el("button", { type: "submit", text: "Send" }));
    form.elements.kind.addEventListener("change", () => updateComposeKind(form));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const kind = form.elements.kind.value;
      const payload = {
        campaign: state.campaign, to: form.elements.to.value, kind,
        summary: form.elements.summary.value.trim(), body: form.elements.body.value,
      };
      if (kind === "notice") {
        payload.action = form.elements.action.value;
        if (form.elements.supersedes.value.trim()) payload.supersedes = form.elements.supersedes.value.trim();
      }
      // The same draft retried keeps its request UUID.
      const key = "send:" + JSON.stringify(payload);
      payload.request_id = requestID(key);
      const result = form.querySelector(".result");
      const button = form.querySelector("button[type=submit]");
      button.disabled = true;
      result.className = "result";
      result.textContent = "Sending…";
      try {
        const reply = await post("/api/swarm/action/send", payload);
        pendingRequests.delete(key);
        result.textContent = "Sent. The agent sees it at its next turn boundary; until it claims the delivery the flow shows it as sent and not yet seen." + outcomeText(reply);
        form.elements.summary.value = "";
        form.elements.body.value = "";
        refreshSnapshot();
      } catch (error) {
        result.className = "result refused";
        result.textContent = refusalText(error) + " Sending again reuses the same request.";
      } finally {
        button.disabled = false;
      }
    });
    return form;
  }

  // The store's messages often start with their code; say the code once.
  function withoutCode(message, code) {
    let text = String(message || "");
    while (code && text.startsWith(code + ": ")) text = text.slice(code.length + 2);
    return text;
  }

  function refusalText(error) {
    const text = withoutCode(error.message, error.code).replace(/\.$/, "");
    return `Refused${error.code ? " (" + error.code + ")" : ""}: ${text}.`;
  }

  const outcomeWords = {
    cancelled: "The store recorded the cancellation.",
    cancel_pending: "A native turn holds the delivery; the store recorded a pending cancellation.",
    already_terminal: "It had already finished before the click; nothing was changed.",
  };

  function deliveryText(delivery) {
    const display = delivery.display || delivery.state || "unknown";
    const shown = displayWords[display] || display;
    return `${labelOf(delivery.consumer)}: ${shown}${delivery.code ? " (" + delivery.code + ")" : ""}`;
  }

  // What the store recorded, as the reply states it.
  function outcomeText(reply) {
    if (!reply || typeof reply !== "object") return "";
    const parts = [];
    if (typeof reply.outcome === "string") parts.push(outcomeWords[reply.outcome] || `Outcome: ${reply.outcome}.`);
    if (reply.delivery && typeof reply.delivery === "object") parts.push(`The store recorded ${deliveryText(reply.delivery)}.`);
    if (reply.wait && typeof reply.wait === "object") parts.push(`The wait is ${reply.wait.state}${reply.wait.terminal_reason ? " (" + reply.wait.terminal_reason + ")" : ""}.`);
    if (Array.isArray(reply.deliveries) && reply.deliveries.length) parts.push("Deliveries: " + reply.deliveries.map(deliveryText).join("; ") + ".");
    if (reply.detail) {
      const detail = String(reply.detail);
      const code = /^[A-Z][A-Z_]+(?=: )/.exec(detail);
      parts.push(code ? withoutCode(detail, code[0]) : detail);
    }
    return parts.length ? " " + parts.join(" ") : "";
  }

  // ------------------------------------------------------------- event detail

  async function openEvent(eventID) {
    state.eventDetail = eventID;
    const event = state.log.get(eventID);
    byID("event-title").textContent = event ? `${event.kind}: ${event.summary || event.event_id}` : "Event " + eventID;
    const body = byID("event-body");
    body.replaceChildren(note("Reading the event…"));
    openDialog("event-detail");
    if (event) body.prepend(eventEnvelope(event));
    try {
      const reply = await api(`/api/swarm/event?campaign=${encodeURIComponent(state.campaign)}&event_id=${encodeURIComponent(eventID)}`);
      if (state.eventDetail !== eventID) return;
      body.replaceChildren(eventEnvelope(reply.event || event || {}));
      const deliveries = reply.deliveries || deliveriesOf(eventID);
      body.append(el("h3", { text: "Recipients" }), deliveries.length ? el("ul", { class: "plain" }, deliveries.map((delivery) => el("li", {},
        deliveryChip(delivery, fromOwner(reply.event || event)), " ", deliveryControls(delivery)))) : note("no delivery"));
      body.append(el("h3", { text: "Message" }));
      if (typeof reply.body === "string") {
        body.append(el("pre", { class: "body", text: reply.body }));
        if (reply.body_truncated) body.append(note("The body was cut at the server's bound.", "unavailable"));
      }
      else body.append(note(reply.body_unavailable ? "The body is unavailable: " + reply.body_unavailable : "No body was returned.", "unavailable"));
    } catch (error) {
      if (state.eventDetail !== eventID) return;
      body.replaceChildren();
      if (event) body.append(eventEnvelope(event));
      const deliveries = deliveriesOf(eventID);
      body.append(el("h3", { text: "Recipients" }), deliveries.length ? el("ul", { class: "plain" }, deliveries.map((delivery) => el("li", {},
        deliveryChip(delivery, fromOwner(event)), " ", deliveryControls(delivery)))) : note("no delivery in the snapshot"));
      body.append(note("The event body could not be read. " + refusalText(error), "unavailable"));
    }
  }

  function eventEnvelope(event) {
    const board = event.to && event.to.type === "topic" && event.to.name === "agent-board" && event.kind === "request";
    return el("div", {}, board ? note("This is a question on the board. Answer it on the board; the page has no control for it.", "warning") : null,
      eventFields(event));
  }

  function eventFields(event) {
    return el("dl", { class: "fields" },
      field("From", senderLabel(event)),
      field("To", destinationLabel(event.to)),
      field("Kind", [event.kind, event.action ? "action " + event.action : "", event.blocker ? "blocker" : ""].filter(Boolean).join(" · ")),
      field("Summary", linked(event.summary || "")),
      field("Created", timeEl(event.created_at)),
      event.causation_id ? field("Answers", el("code", { text: event.causation_id })) : null,
      field("Event", el("code", { text: event.event_id || "" })));
  }

  // ---------------------------------------------------------------- actions

  async function acknowledge(deliveryID, eventID) {
    const key = "ack:" + deliveryID;
    const body = byID("confirm-body");
    byID("confirm-title").textContent = "Acknowledge your delivery";
    body.replaceChildren(
      note("The server claims your oldest eligible delivery and completes it, so your inbox is acknowledged in order."),
      eventID && state.log.get(eventID) ? el("p", {}, "Expected: ", linked(state.log.get(eventID).summary || eventID)) : null);
    prepareConfirm(true, "Acknowledge", async () => {
      let reply;
      try {
        reply = await post("/api/swarm/action/acknowledge", { request_id: requestID(key), campaign: state.campaign, delivery_id: deliveryID });
      } catch (error) {
        if (error.code === "VERSION_CONFLICT" && error.body && error.body.oldest) {
          // Nothing changed: the server claims in order, and the oldest is another item.
          pendingRequests.delete(key);
          const oldest = error.body.oldest;
          const expected = flow().deliveries.find((d) => d.delivery_id === error.body.expected);
          refreshSnapshot();
          throw new APIError(`${error.message}. Nothing was changed. You chose "${expected ? expected.summary || expected.event_id : error.body.expected}", ` +
            `but your oldest delivery is "${oldest.summary || oldest.event_id}" from ${senderLabel(oldest)}. Acknowledge that one first`, error.code, error.status, error.body);
        }
        throw error;
      }
      pendingRequests.delete(key);
      if (!reply || !reply.delivery) return "Your inbox was empty; nothing was acknowledged.";
      return "Acknowledged." + outcomeText(reply);
    });
    openDialog("confirm");
  }

  // A reply to a request addressed to the owner. The send route takes a
  // request or a notice and no causation, so the reply is a review notice to
  // the requester; it does not mark the request answered in the store, and
  // the owner acknowledges the request separately.
  function replyTo(to, eventID, title) {
    byID("confirm-title").textContent = `Send a notice to ${labelOf(to)}`;
    const ids = { summary: uuid(), body: uuid() };
    const summary = el("input", { id: ids.summary, type: "text", maxlength: "280", autocomplete: "off" });
    const message = el("textarea", { id: ids.body, rows: "5" });
    byID("confirm-body").replaceChildren(
      el("p", {}, "In answer to: ", linked(title || (state.log.get(eventID) || {}).summary || eventID)),
      el("div", { class: "compose" },
        el("div", { class: "row" }, el("label", { for: ids.summary, text: "Summary" }), summary),
        el("div", { class: "row" }, el("label", { for: ids.body, text: "Message" }), message)),
      note("This sends a review notice to the requester, who sees it at its next turn boundary. It does not answer the request: a wait on the request keeps waiting. Acknowledge the request when you are done with it."));
    prepareConfirm(true, "Send the notice", async () => {
      const payload = { campaign: state.campaign, to, kind: "notice", action: "review",
        summary: summary.value.trim(), body: message.value };
      if (!payload.summary || !payload.body.trim()) throw new APIError("write a summary and a message", "INVALID_REQUEST", 0);
      // The same reply retried keeps its request UUID.
      const key = "reply:" + JSON.stringify(payload);
      payload.request_id = requestID(key);
      const reply = await post("/api/swarm/action/send", payload);
      pendingRequests.delete(key);
      return "Sent; shown as sent and not yet seen until the agent claims it." + outcomeText(reply);
    });
    openDialog("confirm");
    summary.focus();
  }

  // cancel-wait names the owner the page showed. A 409 with the current wait
  // means it changed: the page shows it and asks again. Nothing is resent.
  async function cancelWait(waitID, shown, changed) {
    const wait = shown || waitByID(waitID);
    byID("confirm-title").textContent = "Cancel the wait";
    byID("confirm-body").replaceChildren(
      changed ? note("The wait changed since it was shown. Review it and confirm again.", "warning") : null,
      wait ? el("p", {}, `${labelOf(wait.owner)} stops waiting for `, waitText(wait)) : el("p", {}, "Wait ", el("code", { text: waitID })),
      note("The waiting agent sees the cancellation at its next turn boundary."));
    if (!wait) {
      byID("confirm-body").append(note("The wait is not in the current view, so its owner cannot be confirmed.", "unavailable"));
      prepareConfirm(false, "Cancel the wait", null);
      openDialog("confirm");
      return;
    }
    // A new confirmation is a new request; a retry of this one keeps its UUID.
    const key = `cancel-wait:${waitID}:${wait.owner}:${changed ? uuid() : ""}`;
    prepareConfirm(true, "Cancel the wait", async () => {
      try {
        const reply = await post("/api/swarm/action/cancel-wait", { request_id: requestID(key), campaign: state.campaign, wait_id: waitID, expected_owner: wait.owner });
        pendingRequests.delete(key);
        return (reply && reply.outcome ? "" : "Done.") + outcomeText(reply);
      } catch (error) {
        if (error.status === 409 && error.body && error.body.wait) {
          pendingRequests.delete(key);
          cancelWait(waitID, error.body.wait, true);
          return { reconfirm: "The wait changed; review it above and confirm again." };
        }
        throw error;
      }
    });
    openDialog("confirm");
  }

  function lookupView(title, lookup) {
    const box = el("div", { class: "lookup" }, el("h4", { text: title }));
    if (!lookup || lookup.status === "unavailable") {
      box.append(note("Unknown: the waits could not be looked up" + (lookup && lookup.reason ? " (" + lookup.reason + ")" : "") + ".", "unavailable"));
      return box;
    }
    if (lookup.status === "empty" || !(lookup.waits || []).length) {
      box.append(note("None: no wait is registered."));
      return box;
    }
    box.append(el("ul", { class: "plain" }, lookup.waits.map((wait) => el("li", {}, `${labelOf(wait.owner)} (${wait.state}) `, waitText(wait)))));
    return box;
  }

  async function cancelDelivery(deliveryID) {
    byID("confirm-title").textContent = "Cancel this recipient's delivery";
    const body = byID("confirm-body");
    body.replaceChildren(note("Reading what the cancel affects…"));
    prepareConfirm(false, "Cancel this delivery", null);
    openDialog("confirm");
    let preview;
    try {
      preview = await api(`/api/swarm/action/cancel-preview?campaign=${encodeURIComponent(state.campaign)}&delivery_id=${encodeURIComponent(deliveryID)}`);
    } catch (error) {
      body.replaceChildren(note("The preview is unavailable, so the cancel cannot be confirmed. " + refusalText(error), "unavailable"));
      return;
    }
    showCancelPreview(deliveryID, preview, false);
  }

  // The confirmation shows exactly what the store previewed, and sends its
  // preview_token. A 409 with a new preview means what was shown changed:
  // the page shows the new preview and asks for a new confirmation.
  function showCancelPreview(shownID, preview, changed) {
    const body = byID("confirm-body");
    const delivery = preview.delivery || {};
    // A handoff can move the delivery: the confirmation targets what the preview shows.
    const deliveryID = delivery.delivery_id || shownID;
    body.replaceChildren(
      changed ? note("What this cancel affects changed since it was shown: the recipient or the waits are different. Review it and confirm again.", "warning") : null,
      el("dl", { class: "fields preview" },
        field("Recipient", el("strong", { text: labelOf(delivery.consumer) }), " ", el("code", { text: delivery.consumer || "" })),
        field("Message", linked(delivery.summary || delivery.event_id || ""), ` from ${senderLabel(delivery)}`),
        field("Now", deliveryChip(delivery, fromOwner(delivery)))),
      note("Only this recipient's delivery is cancelled. Other recipients, and a reply that already counts in a group, are not changed."),
      lookupView("Direct-reply waits on the request", preview.direct_reply_waits));
    const lookups = [preview.direct_reply_waits];
    if (preview.group) {
      const group = preview.group;
      body.append(el("div", { class: "lookup" }, el("h4", { text: "Response group" }),
        el("p", {}, `${group.state}, policy ${group.policy}: ${group.responded} of ${group.expected} replies counted · deadline `, timeEl(group.deadline)),
        el("p", { class: "counts", text: group.recipient_reply_counts ? "This recipient's reply already counts; the cancel does not remove it." : "This recipient's reply does not count yet." })),
        lookupView("Waits on the group's closure", group.closure_waits));
      lookups.push(group.closure_waits);
    }
    const siblings = preview.siblings || [];
    body.append(el("div", { class: "lookup" }, el("h4", { text: "Other recipients (unchanged)" }),
      siblings.length ? el("ul", { class: "plain" }, siblings.map((sibling) => el("li", {}, deliveryChip(sibling, fromOwner(sibling))))) : note("none")));
    if (!preview.cancellable) {
      body.append(note("The store would refuse this cancel now. " + (preview.refusal ? `${preview.refusal.code}: ${withoutCode(preview.refusal.message, preview.refusal.code)}` : ""), "refused"));
      prepareConfirm(false, "Cancel this delivery", null);
      return;
    }
    const reasonID = uuid();
    const reason = el("input", { id: reasonID, type: "text", minlength: "8", maxlength: "4000", value: "Cancelled by the owner from the swarm view", autocomplete: "off" });
    body.append(el("div", { class: "compose" }, el("div", { class: "row" }, el("label", { for: reasonID, text: "Reason (recorded with the cancellation, 8 to 4000 characters)" }), reason)));
    // An unavailable wait lookup needs an explicit extra step.
    const unknown = lookups.some((lookup) => !lookup || lookup.status === "unavailable");
    let acknowledged = null;
    if (unknown) {
      const id = uuid();
      acknowledged = el("input", { id, type: "checkbox", class: "unknown-waits" });
      body.append(el("p", { class: "unknown-step" }, acknowledged, " ",
        el("label", { for: id, text: "The affected waits are unknown. Cancel anyway." })));
    }
    // Each preview is its own confirmation; a retry of it keeps its UUID.
    const token = preview.preview_token || "";
    const key = `cancel-delivery:${deliveryID}:${token || uuid()}`;
    const run = async () => {
      const text = reason.value.trim();
      if (text.length < 8 || text.length > 4000) throw new APIError("the reason must be 8 to 4000 characters", "INVALID_REQUEST", 0);
      const attempt = key + ":" + text;
      const request = { request_id: requestID(attempt), campaign: state.campaign, delivery_id: deliveryID, reason: text };
      if (token) request.preview_token = token;
      try {
        const reply = await post("/api/swarm/action/cancel-delivery", request);
        pendingRequests.delete(attempt);
        return (reply && reply.outcome ? "" : "Done.") + outcomeText(reply);
      } catch (error) {
        if (error.status === 409 && error.code === "VERSION_CONFLICT" && error.body && error.body.preview) {
          pendingRequests.delete(attempt);
          showCancelPreview(deliveryID, error.body.preview, true);
          return { reconfirm: "What the cancel affects changed; review it above and confirm again." };
        }
        throw error;
      }
    };
    prepareConfirm(!unknown, "Cancel this delivery", run);
    if (acknowledged) acknowledged.addEventListener("change", () => { byID("confirm-go").disabled = !acknowledged.checked; });
  }

  function prepareConfirm(enabled, label, run) {
    const go = byID("confirm-go");
    const result = byID("confirm-result");
    result.textContent = "";
    result.className = "";
    go.textContent = label;
    go.disabled = !enabled;
    byID("confirm-keep").textContent = "Keep it";
    go.onclick = run ? async () => {
      go.disabled = true;
      result.className = "";
      result.textContent = "Working…";
      try {
        const outcome = await run();
        if (outcome && outcome.reconfirm) {
          // What was shown changed: a new confirmation is required.
          result.textContent = outcome.reconfirm;
          return;
        }
        result.textContent = outcome;
        go.hidden = true;
        byID("confirm-keep").textContent = "Close";
        refreshSnapshot();
      } catch (error) {
        result.className = "refused";
        result.textContent = refusalText(error) + " Trying again reuses the same request.";
        go.disabled = false;
      }
    } : null;
    go.hidden = false;
  }

  // Ages move on without a new message.
  setInterval(() => {
    for (const node of document.querySelectorAll("time[data-age]")) node.textContent = age(Number(node.dataset.age));
    if (state.campaign && state.snapshot) renderObservations();
    if (state.source && state.source.readyState === EventSource.OPEN && state.lastMessageAt && Date.now() - state.lastMessageAt > 15000) {
      setLive("stale", "no message for " + age(state.lastMessageAt).replace(" ago", ""));
    }
  }, 1000);

  // Exposed for the acceptance fixture: read-only counts, no control.
  window.cairnSwarmView = Object.freeze({
    logIDs: () => [...state.log.keys()],
    cursor: () => state.cursor,
    resets: () => state.resets.slice(),
    faults: () => state.faults,
    formatWindow,
    canWrite,
  });

  start();
})();
