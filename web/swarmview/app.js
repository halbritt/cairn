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
    on("tick", (data) => {
      state.dbObservedAt = data.db_observed_at || state.dbObservedAt;
      state.paneTick = data.panes || state.paneTick;
      state.fault = null;
      hideFault();
      renderObservations();
    });
    on("fault", (data) => {
      state.fault = data;
      showFault(`The store could not be read (${data.code || "error"}): ${data.error || ""}. Showing the last snapshot; the server keeps trying.`);
      renderObservations();
    });
    on("reset", (data) => {
      // The stream cannot continue: start a new one without a cursor.
      showFault(`The stream restarted (${data.code || "reset"}): ${data.error || ""}`);
      state.cursor = "";
      openStream("");
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
    const active = members().filter((member) => member.active).length;
    const meta = byID("campaign-meta");
    meta.hidden = false;
    meta.replaceChildren(
      `${active} active member${active === 1 ? "" : "s"} of ${members().length} · started `,
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
        unavailable ? "unavailable" + (panes.error ? ": " + panes.error : "") : ""));
    }

    const room = state.snapshot && state.snapshot.context_room;
    const counters = room ? parseTime(room.observed_at) : null;
    if (sectionError("context_room")) list.append(observation("Session counters", null, true, "unavailable: " + sectionError("context_room")));
    else list.append(observation("Session counters", counters, false, counters === null ? "no counters recorded" : ""));
  }

  function observation(name, time, stale, detail) {
    const item = el("li", { class: stale ? "stale" : "" }, el("strong", { text: name + ": " }));
    if (time !== null) item.append(timeEl(time));
    if (stale) item.append(el("span", { class: "flag", text: " stale" }));
    if (detail) item.append(el("span", { class: "muted", text: (time !== null ? " · " : "") + detail }));
    return item;
  }

  // ---------------------------------------------------------------- needs you

  function renderNeeds(container, needs, unavailableReason, operatorPrincipal) {
    container.replaceChildren();
    if (unavailableReason) {
      container.append(note("Needs you is unavailable: " + unavailableReason, "unavailable"));
      return;
    }
    const items = (needs && needs.items) || [];
    const unavailable = (needs && needs.unavailable) || [];
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

  function needRow(need, operatorPrincipal) {
    const action = need.action || {};
    const refs = need.refs || {};
    const row = el("li", { class: "need", dataset: { need: need.id || "", type: need.type || "" } },
      el("div", { class: "need-head" },
        el("span", { class: "tag", text: needLabel(need.type) }),
        el("strong", {}, linked(need.title || "")),
        need.count > 1 ? el("span", { class: "count", text: ` ×${need.count}` }) : null),
      need.detail ? el("p", { class: "detail" }, linked(need.detail)) : null,
      el("p", { class: "meta" },
        need.subject ? `${labelOf(need.subject)} · ` : "",
        "since ", timeEl(need.since),
        need.expires_at ? [" · expires ", timeEl(need.expires_at)] : null,
        need.waiters ? ` · ${need.waiters} session${need.waiters === 1 ? "" : "s"} waiting on it` : ""));
    const controls = el("div", { class: "need-action" }, el("span", { class: "action-label", text: "Action: " + (action.label || action.kind || "inspect") }));
    const delivery = (refs.delivery_ids || [])[0];
    if (action.kind === "acknowledge" && delivery) {
      controls.append(el("button", { type: "button", text: "Acknowledge", onclick: () => acknowledge(delivery, refs.event_id) }));
    } else if (action.kind === "cancel_delivery" && delivery) {
      controls.append(el("button", { type: "button", text: "Cancel this recipient's delivery", onclick: () => cancelDelivery(delivery) }));
    } else if (action.kind === "cancel_wait" && refs.wait_id) {
      controls.append(el("button", { type: "button", text: "Cancel the wait", onclick: () => cancelWait(refs.wait_id) }));
    } else if ((action.kind === "reply" || action.kind === "answer_board" || action.kind === "decide") && need.subject && state.campaign) {
      controls.append(el("button", { type: "button", text: `Write to ${labelOf(need.subject)}`, onclick: () => openDetail(need.subject, true) }));
    }
    if (refs.event_id && state.campaign) controls.append(el("button", { type: "button", class: "secondary", text: "Open the event", onclick: () => openEvent(refs.event_id) }));
    if (action.command) controls.append(copyButton(action.command, "Copy command"));
    row.append(controls);
    return row;
  }

  // ------------------------------------------------------------------- agents

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

    const shown = members().filter((member) => !(handedOff.has(member.member) && !member.active && !holders.has(member.member)));
    if (!shown.length) container.append(note("This campaign has no members."));
    for (const member of shown) container.append(agentRow(member));

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

  function field(name, ...value) {
    return el("div", { class: "field" }, el("dt", { text: name }), el("dd", {}, ...value));
  }

  function agentRow(member) {
    const session = member.session;
    const memberFlow = flow().by_member[member.member];
    const label = member.label || shortPrincipal(member.member);
    const role = member.role_label || member.role || "member";
    const selected = state.filter === member.member;
    const card = el("article", { class: `agent${member.active ? "" : " inactive"}${selected ? " selected" : ""}`, dataset: { member: member.member, label }, "aria-label": label });
    card.append(el("header", { class: "agent-head" },
      el("h3", { text: label }),
      el("span", { class: "role", text: role }),
      member.role_label && member.role ? el("span", { class: "muted", text: ` (${member.role})` }) : null,
      member.role_address ? el("code", { class: "role-address", text: member.role_address }) : null,
      member.active ? null : el("span", { class: "tag", text: "inactive member" })));

    const fields = el("dl", { class: "fields" });
    if (session) {
      fields.append(field("Harness", [session.harness, session.observed_model || session.model].filter(Boolean).join(" · ") || "not reported"));
      fields.append(field("State", presenceText(session)));
      fields.append(field("Task", session.task_summary ? longText(session.task_summary) : el("span", { class: "muted", text: "no task reported" })));
    } else {
      fields.append(field("Session", el("span", { class: "muted", text: "no registered session for this principal" })));
    }
    fields.append(field("Holds", holdsView(member, memberFlow)));
    fields.append(field("Waits on", waitsOnView(member, memberFlow)));
    fields.append(field("Waited on", waitedOnView(member, memberFlow)));
    fields.append(field("Context room", contextView(member)));
    fields.append(field("Pane", paneView(member)));
    const issues = member.plane_issues || [];
    if (issues.length) fields.append(field("Plane", issues.map((id, i) => [i ? ", " : "", planeLink(id)])));
    const decisions = ((state.snapshot.needs_you && state.snapshot.needs_you.lead_decisions) || {})[member.member] || [];
    if (decisions.length) {
      fields.append(field("Decisions to make", el("ul", { class: "plain" }, decisions.map((d) => el("li", {}, el("strong", { text: d.item_key + ": " }), linked(d.summary), " · ", timeEl(d.updated_at))))));
    }
    card.append(fields);
    card.append(el("div", { class: "agent-actions" },
      el("button", { type: "button", "aria-pressed": selected ? "true" : "false", text: selected ? "Show all messages" : "Show its messages", onclick: () => setFilter(selected ? "" : member.member) }),
      el("button", { type: "button", text: "Details and actions", onclick: () => openDetail(member.member) })));
    return card;
  }

  function presenceText(session) {
    const parts = [session.presence || "unknown"];
    if (session.state) parts.push(session.state);
    const text = parts.join(", ");
    if (session.presence !== "online" && session.last_seen) return el("span", {}, text + " · last seen ", timeEl(session.last_seen));
    return el("span", { class: `presence presence-${session.presence || "unknown"}`, text });
  }

  function holdsView(member, memberFlow) {
    const holds = member.holds || [];
    const parts = [];
    if (holds.length) {
      parts.push(el("ul", { class: "plain" }, holds.map((hold) => el("li", {},
        el("span", { class: "tag", text: hold.kind }), " ", el("strong", { text: hold.item_key }), ` (${hold.status}) `, linked(hold.summary)))));
    }
    if (memberFlow && (memberFlow.leased || memberFlow.held)) {
      parts.push(el("p", { text: `${memberFlow.leased || 0} leased deliver${memberFlow.leased === 1 ? "y" : "ies"}${memberFlow.held ? `, ${memberFlow.held} held` : ""}` }));
    }
    if (!parts.length) parts.push(el("span", { class: "muted", text: "nothing open" }));
    return parts;
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
    if (!ids.length) return el("span", { class: "muted", text: flow().waits_feature === "active" ? "no registered wait" : `no registered wait (waits feature ${flow().waits_feature})` });
    return el("ul", { class: "plain" }, ids.map((id) => el("li", {}, waitText(waitByID(id)))));
  }

  function waitedOnView(member, memberFlow) {
    if (sectionError("flow")) return el("span", { class: "unavailable", text: "unavailable" });
    const parts = [];
    if (memberFlow && memberFlow.oldest_pending) {
      const delivery = flow().deliveries.find((d) => d.delivery_id === memberFlow.oldest_pending);
      parts.push(el("p", {}, el("strong", { text: "Oldest pending: " }),
        delivery ? [linked(delivery.summary || delivery.kind), ` from ${labelOf(delivery.from)}, `, timeEl(delivery.created_at)] : memberFlow.oldest_pending,
        memberFlow.pending > 1 ? ` (${memberFlow.pending} pending)` : ""));
    }
    const peers = (memberFlow && memberFlow.peer_waits) || [];
    if (peers.length) {
      parts.push(el("p", {}, el("strong", { text: "A peer waits on it: " })));
      parts.push(el("ul", { class: "plain" }, peers.map((id) => {
        const wait = waitByID(id);
        return el("li", {}, wait ? `${labelOf(wait.owner)} waits for ` : "", waitText(wait));
      })));
    }
    if (!parts.length) parts.push(el("span", { class: "muted", text: "nothing pending" }));
    return parts;
  }

  function contextView(member) {
    if (sectionError("context_room")) return el("span", { class: "unavailable", text: "unavailable: " + sectionError("context_room") });
    const room = (state.snapshot.context_room && state.snapshot.context_room.by_agent) || {};
    const entry = member.agent_id ? room[member.agent_id] : null;
    if (!entry || !entry.known) return el("span", { class: "muted", text: "unknown" });
    const parts = [];
    if (entry.near_limit) parts.push(el("p", { class: "near-limit" }, el("strong", { text: "⚠ near its limit" }), entry.reasons && entry.reasons.length ? `: ${entry.reasons.join("; ")}` : ""));
    if (entry.injected_bytes !== undefined && entry.injected_bytes !== null) {
      const counts = [];
      if (entry.injected_deliveries !== undefined && entry.injected_deliveries !== null) counts.push(`${entry.injected_deliveries} deliveries`);
      if (entry.injected_recalls !== undefined && entry.injected_recalls !== null) counts.push(`${entry.injected_recalls} recalls`);
      parts.push(el("p", {}, "Cairn-injected since compaction: ", el("strong", { text: bytes(entry.injected_bytes) }), counts.length ? ` (${counts.join(", ")})` : ""));
    } else {
      parts.push(el("p", { text: "Cairn-injected since compaction: unknown" }));
    }
    parts.push(el("p", {}, "Compactions: ", entry.compactions === undefined || entry.compactions === null ? "unknown" : String(entry.compactions),
      " · last ", entry.last_compaction_at ? timeEl(entry.last_compaction_at) : el("span", { class: "muted", text: "unknown" })));
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
    if (panes.status === "unavailable") return el("span", { class: "unavailable", text: "Herdr unavailable" + (panes.error ? ": " + panes.error : "") });
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
    const metaParts = [`${state.log.size} event${state.log.size === 1 ? "" : "s"} in the window (newest ${state.logWindow} kept)`];
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

  function deliveryChip(delivery, ownerSent) {
    const display = delivery.display || delivery.state || "unknown";
    const chip = el("span", { class: `chip chip-${display}`, dataset: { delivery: delivery.delivery_id, display } },
      `${labelOf(delivery.consumer)}: `, el("span", { "aria-hidden": "true", text: (displayMarks[display] || "?") + " " }), display);
    if (delivery.code) chip.append(` (${delivery.code})`);
    if (ownerSent && !delivery.seen && openDisplays.has(display)) chip.append(el("span", { class: "unseen", text: " · sent, not yet seen" }));
    if (delivery.replied) chip.append(" · replied");
    return chip;
  }

  function flowRow(event, ledgerItems) {
    const deliveries = deliveriesOf(event.event_id);
    const ownerSent = event.from === operator();
    const row = el("li", { class: `event event-${event.kind}${ownerSent ? " owner-sent" : ""}`, dataset: { event: event.event_id, position: String(event.position) } });
    row.append(el("div", { class: "event-head" },
      el("span", { class: "tag kind", text: event.kind }),
      event.action ? el("span", { class: "tag", text: "action " + event.action }) : null,
      event.blocker ? el("span", { class: "tag flag", text: "blocker" }) : null,
      event.grouped ? el("span", { class: "tag", text: "grouped" }) : null,
      el("span", { class: "route", text: `${labelOf(event.from)} → ${destinationLabel(event.to)}` }),
      el("span", { class: "muted" }, " · ", timeEl(event.created_at))));
    row.append(el("p", { class: "summary" }, event.summary ? linked(event.summary) : el("span", { class: "muted", text: "no summary" })));
    const states = el("div", { class: "deliveries", "aria-label": "Delivery state of each recipient" });
    if (deliveries.length) states.append(...deliveries.map((delivery) => deliveryChip(delivery, ownerSent)));
    else states.append(el("span", { class: "muted", text: sectionError("flow") ? "delivery state unavailable" : "no delivery in the snapshot" }));
    row.append(states);
    if (event.supersedes) row.append(el("p", { class: "meta", text: "topic key " + event.supersedes }));
    if (ledgerItems.length) {
      row.append(el("p", { class: "ledger-link" }, "Ledger: ", ledgerItems.map((item, i) => [i ? ", " : "", `${item.kind} ${item.item_key} (${item.status})`])));
    }
    const actions = el("div", { class: "event-actions" }, el("button", { type: "button", class: "secondary", text: "Open", onclick: () => openEvent(event.event_id) }));
    if (ownerSent && event.kind === "notice" && event.supersedes && event.to && event.to.type === "agent") {
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
      el("span", { class: "tag", text: event.kind }), ` ${labelOf(event.from)} → ${destinationLabel(event.to)}: `, linked(event.summary || ""), " · ", timeEl(event.created_at), " ",
      el("button", { type: "button", class: "secondary", text: "Open", onclick: () => openEvent(event.event_id) }))))
      : note("none in the window"));

    body.append(el("h3", { text: "Write to " + label }));
    body.append(draft || composeForm(principal));
  }

  function deliveryList(list, incoming) {
    if (sectionError("flow")) return note("unavailable: " + sectionError("flow"), "unavailable");
    if (!list.length) return note("none");
    return el("ul", { class: "plain" }, list.map((delivery) => el("li", {},
      deliveryChip(delivery, delivery.from === operator()), " ",
      el("span", { class: "tag", text: delivery.kind }), " ",
      linked(delivery.summary || ""), ` · ${incoming ? "from " + labelOf(delivery.from) : "to " + labelOf(delivery.consumer)} · `, timeEl(delivery.created_at), " ",
      deliveryControls(delivery))));
  }

  function deliveryControls(delivery) {
    const controls = [];
    if (delivery.consumer === operator() && openDisplays.has(delivery.display || delivery.state)) {
      controls.push(el("button", { type: "button", text: "Acknowledge", onclick: () => acknowledge(delivery.delivery_id, delivery.event_id) }));
    } else if (openDisplays.has(delivery.display || delivery.state)) {
      controls.push(el("button", { type: "button", text: "Cancel this recipient's delivery", onclick: () => cancelDelivery(delivery.delivery_id) }));
    }
    return controls;
  }

  function updateComposeKind(form) {
    const notice = form.elements.kind.value === "notice";
    form.querySelector(".notice-only").hidden = !notice;
  }

  function composeForm(principal) {
    const form = el("form", { class: "compose", dataset: { principal } });
    const ids = { kind: uuid(), action: uuid(), summary: uuid(), body: uuid(), supersedes: uuid() };
    form.append(
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
        campaign: state.campaign, to: principal, kind,
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
      } catch (error) {
        result.className = "result refused";
        result.textContent = refusalText(error) + " Sending again reuses the same request.";
      } finally {
        button.disabled = false;
      }
    });
    return form;
  }

  function refusalText(error) {
    return `Refused${error.code ? " (" + error.code + ")" : ""}: ${error.message}.`;
  }

  const outcomeWords = {
    cancelled: "The store recorded the cancellation.",
    cancel_pending: "A native turn holds the delivery; the store recorded a pending cancellation.",
    already_terminal: "It had already finished before the click; nothing was changed.",
  };

  function deliveryText(delivery) {
    const shown = delivery.display || delivery.state || "unknown";
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
    if (reply.detail) parts.push(String(reply.detail));
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
        deliveryChip(delivery, (reply.event || event || {}).from === operator()), " ", deliveryControls(delivery)))) : note("no delivery"));
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
        deliveryChip(delivery, event && event.from === operator()), " ", deliveryControls(delivery)))) : note("no delivery in the snapshot"));
      body.append(note("The event body could not be read. " + refusalText(error), "unavailable"));
    }
  }

  function eventEnvelope(event) {
    return el("dl", { class: "fields" },
      field("From", labelOf(event.from)),
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
          throw new APIError(`${error.message}. Nothing was changed. Your oldest delivery is another one: "${oldest.summary || oldest.event_id}" from ${labelOf(oldest.from)}. Acknowledge that one first`, error.code, error.status, error.body);
        }
        throw error;
      }
      pendingRequests.delete(key);
      if (!reply || !reply.delivery) return "Your inbox was empty; nothing was acknowledged.";
      return "Acknowledged." + outcomeText(reply);
    });
    openDialog("confirm");
  }

  async function cancelWait(waitID) {
    const key = "cancel-wait:" + waitID;
    const wait = waitByID(waitID);
    byID("confirm-title").textContent = "Cancel the wait";
    byID("confirm-body").replaceChildren(
      wait ? el("p", {}, `${labelOf(wait.owner)} stops waiting for `, waitText(wait)) : el("p", {}, "Wait ", el("code", { text: waitID })),
      note("The waiting agent sees the cancellation at its next turn boundary."));
    prepareConfirm(true, "Cancel the wait", async () => {
      const reply = await post("/api/swarm/action/cancel-wait", { request_id: requestID(key), campaign: state.campaign, wait_id: waitID });
      pendingRequests.delete(key);
      return (reply && reply.outcome ? "" : "Done.") + outcomeText(reply);
    });
    openDialog("confirm");
  }

  function lookupView(title, lookup) {
    const box = el("div", { class: "lookup" }, el("h4", { text: title }));
    if (!lookup || lookup.status === "unavailable") {
      box.append(note("Could not be looked up" + (lookup && lookup.reason ? ": " + lookup.reason : "") + ". Some waits may be affected.", "unavailable"));
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
    const key = "cancel-delivery:" + deliveryID;
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
    const delivery = preview.delivery || {};
    body.replaceChildren(
      el("dl", { class: "fields preview" },
        field("Recipient", el("strong", { text: labelOf(delivery.consumer) }), " ", el("code", { text: delivery.consumer || "" })),
        field("Message", linked(delivery.summary || delivery.event_id || ""), ` from ${labelOf(delivery.from)}`),
        field("Now", deliveryChip(delivery, delivery.from === operator()))),
      note("Only this recipient's delivery is cancelled. Other recipients, and a reply that already counts in a group, are not changed."),
      lookupView("Direct-reply waits on the request", preview.direct_reply_waits));
    if (preview.group) {
      const group = preview.group;
      body.append(el("div", { class: "lookup" }, el("h4", { text: "Response group" }),
        el("p", {}, `${group.state}, policy ${group.policy}: ${group.responded} of ${group.expected} replies counted · deadline `, timeEl(group.deadline)),
        el("p", { class: "counts", text: group.recipient_reply_counts ? "This recipient's reply already counts; the cancel does not remove it." : "This recipient's reply does not count yet." })),
        lookupView("Waits on the group's closure", group.closure_waits));
    }
    const siblings = preview.siblings || [];
    body.append(el("div", { class: "lookup" }, el("h4", { text: "Other recipients (unchanged)" }),
      siblings.length ? el("ul", { class: "plain" }, siblings.map((sibling) => el("li", {}, deliveryChip(sibling, sibling.from === operator())))) : note("none")));
    if (!preview.cancellable) {
      body.append(note("The store would refuse this cancel now. " + (preview.refusal ? `${preview.refusal.code}: ${preview.refusal.message}` : ""), "refused"));
      return;
    }
    const reasonID = uuid();
    const reason = el("input", { id: reasonID, type: "text", minlength: "8", maxlength: "4000", value: "Cancelled by the owner from the swarm view", autocomplete: "off" });
    body.append(el("div", { class: "compose" }, el("div", { class: "row" }, el("label", { for: reasonID, text: "Reason (recorded with the cancellation)" }), reason)));
    prepareConfirm(true, "Cancel this delivery", async () => {
      const text = reason.value.trim();
      if (text.length < 8 || text.length > 4000) throw new APIError("the reason must be 8 to 4000 characters", "INVALID_REQUEST", 0);
      // A retry keeps the request UUID; a changed reason is a new request.
      const attempt = key + ":" + text;
      const reply = await post("/api/swarm/action/cancel-delivery", { request_id: requestID(attempt), campaign: state.campaign, delivery_id: deliveryID, reason: text });
      pendingRequests.delete(attempt);
      return (reply && reply.outcome ? "" : "Done.") + outcomeText(reply);
    });
  }

  function prepareConfirm(enabled, label, run) {
    const go = byID("confirm-go");
    const result = byID("confirm-result");
    result.textContent = "";
    result.className = "";
    go.textContent = label;
    go.disabled = !enabled;
    go.onclick = run ? async () => {
      go.disabled = true;
      result.className = "";
      result.textContent = "Working…";
      try {
        result.textContent = await run();
        go.hidden = true;
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
  });

  start();
})();
