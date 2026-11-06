(() => {
  "use strict";
  const byID = id => document.getElementById(id);
  const groups = ["backlog", "unstarted", "started", "completed", "cancelled", "unknown"];
  const pageSize = 50;
  let currentProject = "";
  let currentItemUUID = "";
  let currentPage = 1;
  let totalItems = 0;
  let csrfToken = "";
  let boardRequest = 0;
  let detailRequest = 0;
  let previousFocus = null;
  let writing = false;
  let refreshTimer = null;
  let presenceTimer = null;
  let projectRefreshBlocked = true;
  let currentProjectStatus = "";
  const pendingWrites = new Map();

  function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[char]);
  }

  function message(id, text = "") {
    const node = byID(id);
    node.textContent = text;
    node.hidden = !text;
  }

  function dateLabel(value) {
    const date = new Date(value);
    return value && !Number.isNaN(date.getTime()) ? date.toLocaleString() : "Unknown";
  }

  function syncModalState() {
    byID("work-app").inert = !byID("login-modal").hidden || !byID("detail-modal").hidden;
  }

  function showLogin() {
    csrfToken = "";
    closeDetail();
    byID("login-modal").hidden = false;
    syncModalState();
    byID("launch-code-input").focus();
  }

  function closeDetail() {
    const item = currentItemUUID;
    detailRequest++;
    clearTimeout(presenceTimer);
    currentItemUUID = "";
    byID("detail-modal").hidden = true;
    syncModalState();
    if (byID("login-modal").hidden) {
      const card = [...document.querySelectorAll(".work-card")].find(node => node.dataset.itemUuid === item);
      (previousFocus?.isConnected ? previousFocus : card || byID("project-select")).focus();
    }
  }

  async function apiFetch(path, options = {}) {
    const headers = new Headers(options.headers);
    if (options.method === "POST" && csrfToken) headers.set("X-CSRF-Token", csrfToken);
    const response = await fetch(path, { ...options, headers, cache: "no-store" });
    const token = response.headers.get("X-CSRF-Token");
    if (token) csrfToken = token;
    const body = await response.json();
    if (!response.ok) {
      if (response.status === 401) showLogin();
      throw new Error(body.message || body.error || `Request failed (${response.status})`);
    }
    return body;
  }

  async function loadProjects() {
    try {
      const response = await apiFetch("/api/work/projects");
      const select = byID("project-select");
      select.replaceChildren();
      for (const project of response.projects || []) {
        const option = document.createElement("option");
        option.value = project.local_slug;
        option.textContent = `${project.local_slug} (${project.plane_origin})`;
        select.append(option);
      }
      if (!select.options.length) {
        select.add(new Option("No projects configured", ""));
        select.disabled = true;
        byID("refresh-btn").disabled = true;
        message("empty-state", "No projects configured. Add a project with cairn work project add, then reload this view.");
        byID("staleness-indicator").textContent = "No project selected";
        return;
      }
      select.disabled = false;
      byID("refresh-btn").disabled = false;
      currentProject = select.value;
      currentPage = 1;
      await loadBoard();
    } catch (error) {
      message("view-error", "Could not load projects: " + error.message);
    }
  }

  async function loadBoard(preserveError = false) {
    if (!currentProject) return;
    clearTimeout(refreshTimer);
    currentProjectStatus = "";
    updateWorkControls();
    const sequence = ++boardRequest;
    const query = new URLSearchParams({ project: currentProject, limit: pageSize, offset: (currentPage - 1) * pageSize });
    if (byID("filter-state-group").value) query.set("state_group", byID("filter-state-group").value);
    if (byID("filter-blocker-only").checked) query.set("blocker_only", "1");
    try {
      const data = await apiFetch("/api/work/items?" + query);
      if (sequence !== boardRequest) return;
      totalItems = data.total ?? (data.items || []).length;
      byID("page-info").textContent = `Page ${currentPage} of ${Math.max(1, Math.ceil(totalItems / pageSize))}`;
      byID("page-prev-btn").disabled = currentPage <= 1;
      byID("page-next-btn").disabled = currentPage * pageSize >= totalItems;
      if (!preserveError) message("view-error");
      renderBoard(data);
    } catch (error) {
      if (sequence !== boardRequest) return;
      message("view-error", "Could not load work items: " + error.message);
      const freshness = byID("staleness-indicator");
      const last = freshness.dataset.project === currentProject ? freshness.dataset.lastSuccessfulRefresh : "";
      freshness.className = "badge stale";
      freshness.textContent = "Freshness unknown; last read failed" + (last ? "; last successful refresh " + dateLabel(last) : "");
    }
  }

  function renderBoard(data) {
    const items = data.items || [];
    const project = data.project || {};
    currentProjectStatus = project.status || "";
    byID("auth-alert").hidden = !project.auth_rejected;
    const freshness = byID("staleness-indicator");
    const failed = project.last_refresh_error || items.some(item => item.refresh_error);
    const stale = project.is_stale || items.some(item => item.is_stale);
    const last = project.last_successful_refresh_at;
    freshness.dataset.project = currentProject;
    freshness.dataset.lastSuccessfulRefresh = last || "";
    freshness.className = "badge";
    if (project.auth_rejected) {
      freshness.textContent = "Plane access restricted";
      freshness.classList.add("stale");
    } else if (failed || stale) {
      freshness.textContent = `${failed ? "Refresh failed" : "Stale cache"}${last ? "; last success " + dateLabel(last) : "; no successful refresh"}`;
      freshness.classList.add("stale");
    } else if (last) {
      freshness.textContent = "Last refreshed " + dateLabel(last);
      freshness.classList.add("fresh");
    } else {
      freshness.textContent = "No successful refresh";
    }
    message("refresh-error", project.last_refresh_error && !project.auth_rejected
      ? "Refresh failed: " + project.last_refresh_error : "");
    const nextAllowed = Date.parse(project.next_refresh_allowed_at);
    const inFlightExpires = Date.parse(project.refresh_inflight_expires_at);
    const now = Date.now();
    const limited = nextAllowed > now;
    projectRefreshBlocked = Boolean(project.refresh_in_flight || limited);
    updateWorkControls();
    message("refresh-availability", project.refresh_in_flight
      ? "A refresh is running. This view shows the last saved snapshot."
      : limited ? "Refresh available after " + dateLabel(project.next_refresh_allowed_at) + "." : "");
    const staleAt = last && !stale && !failed && !project.auth_rejected
      ? Math.max(now + 1000, Date.parse(last) + 120000) : NaN;
    const deadlines = [nextAllowed, project.refresh_in_flight ? inFlightExpires : NaN, staleAt].filter(value => value > now);
    if (deadlines.length) refreshTimer = setTimeout(loadBoard, Math.max(1000, Math.min(60000, Math.min(...deadlines) - now + 100)));
    const filtered = byID("filter-state-group").value || byID("filter-blocker-only").checked;
    message("empty-state", items.length ? "" : filtered ? "No items match the current filters." : "No work items in this project.");
    byID("board-summary").textContent = `${items.length} of ${totalItems} items shown. States come from Plane; notes and links are local to Cairn.`;
    const counts = Object.fromEntries(groups.map(group => [group, 0]));
    for (const group of groups) byID("col-" + group).replaceChildren();
    for (const item of items) {
      const group = groups.includes(item.state_group) ? item.state_group : "unknown";
      counts[group]++;
      const card = document.createElement("div");
      card.className = "work-card";
      card.dataset.itemUuid = item.plane_item_uuid;
      card.dataset.identifier = item.identifier || "";
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.innerHTML = `<div class="card-id">${escapeHTML(item.identifier || item.plane_item_uuid)}</div>
        <div class="card-title">${escapeHTML(item.title)}</div>
        <div class="card-footer"><span class="badge">${escapeHTML(item.state_name || item.state_group || "State unavailable")}</span>
        ${item.is_absent ? '<span class="badge stale">Absent from latest snapshot</span>' : ''}</div>`;
      card.addEventListener("click", () => showDetail(item.plane_item_uuid));
      card.addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          showDetail(item.plane_item_uuid);
        }
      });
      byID("col-" + group).append(card);
    }
    for (const group of groups) {
      byID("count-" + group).textContent = counts[group];
      const column = byID("col-" + group).parentElement;
      column.dataset.empty = counts[group] === 0;
      column.hidden = group === "unknown" ? counts[group] === 0 : items.length > 0 && counts.unknown === items.length;
    }
  }

  function updateWorkControls() {
    const admitted = currentProjectStatus === "active";
    byID("refresh-btn").disabled = writing || !admitted || projectRefreshBlocked;
    const label = admitted ? "" : currentProjectStatus
      ? `Project ${currentProjectStatus}. Refresh, new associations and annotation changes are disabled. Retained details and association removal remain available.`
      : "Project admission has not been checked. Refresh and new work changes are unavailable.";
    message("project-admission", label);
    message("detail-admission", label);
    for (const node of document.querySelectorAll("#add-assoc-btn, #add-progress-btn, #add-blocker-btn, .edit-progress-btn, .save-progress-btn, .resolve-blocker-btn")) {
      node.disabled = writing || !admitted || node.dataset.guardInvalid === "true";
    }
  }

  function updatePresenceLabels() {
    clearTimeout(presenceTimer);
    const now = Date.now(), deadlines = [];
    for (const node of document.querySelectorAll(".session-presence-status")) {
      const expiry = Date.parse(node.dataset.expiresAt);
      node.textContent = node.dataset.historical === "true" ? "Historical execution"
        : node.dataset.online !== "true" ? "Offline or expired at last read"
        : !Number.isFinite(expiry) ? "Presence expiry unavailable"
        : expiry <= now ? "Last observed presence expired" : "Online at last read";
      if (node.dataset.online === "true" && expiry > now) deadlines.push(expiry);
    }
    if (deadlines.length) presenceTimer = setTimeout(updatePresenceLabels, Math.max(100, Math.min(60000, Math.min(...deadlines) - now + 100)));
  }

  function renderLinkedState(detail) {
    byID("sessions-section").hidden = !(detail.sessions || []).length;
    byID("modal-sessions").replaceChildren();
    for (const session of detail.sessions || []) {
      const row = document.createElement("li");
      row.className = "session-item";
      row.dataset.agentId = session.agent_id;
      row.dataset.executionId = session.execution_id;
      row.innerHTML = `<strong class="session-presence-status" data-historical="${session.is_historical === true}" data-online="${session.online === true}" data-expires-at="${escapeHTML(session.expires_at || "")}"></strong>
        <div>${escapeHTML(session.task_summary || "No selected summary")}</div>
        <span class="hint">Read at ${escapeHTML(dateLabel(new Date().toISOString()))}. Presence expiry: <time class="session-expiry" datetime="${escapeHTML(session.expires_at || "")}">${escapeHTML(dateLabel(session.expires_at))}</time>.</span>
        <span class="hint">${escapeHTML(session.agent_id)}:${escapeHTML(session.execution_id)}</span>`;
      byID("modal-sessions").append(row);
    }
    updatePresenceLabels();
    byID("requests-section").hidden = !(detail.requests || []).length;
    byID("modal-requests").replaceChildren();
    for (const request of detail.requests || []) {
      const row = document.createElement("li");
      row.className = "request-item";
      row.dataset.eventId = request.event_id;
      row.innerHTML = `${payloadMarkup(request)}
        <div>Event kind: ${escapeHTML(request.event_kind || "unknown")}</div>
        <div class="delivery-summary">Delivery: ${escapeHTML(request.delivery_state || "unknown")}</div>
        <span class="hint">Event ${escapeHTML(request.event_id)}</span>`;
      if ((request.deliveries || []).length) {
        const deliveries = document.createElement("ul");
        for (const delivery of request.deliveries) {
          const entry = document.createElement("li");
          entry.className = "delivery-item";
          entry.dataset.deliveryId = delivery.delivery_id;
          entry.textContent = `${delivery.consumer}: ${delivery.state}${delivery.code ? " (" + delivery.code + ")" : ""}`;
          deliveries.append(entry);
        }
        row.append(deliveries);
      }
      byID("modal-requests").append(row);
    }
    renderTargets("response-groups", detail.response_groups, group => {
      const row = document.createElement("li");
      row.className = "response-group-item";
      row.dataset.groupEventId = group.group_event_id;
      row.innerHTML = `<strong class="target-state">Group state: ${escapeHTML(group.state || "unknown")}</strong>
        <div>Responses: ${escapeHTML(group.responded_count)} of ${escapeHTML(group.member_count)} · policy: ${escapeHTML(group.partial_policy || "unknown")}</div>
        ${group.code ? `<div>Closure code: ${escapeHTML(group.code)}</div>` : ""}
        ${group.deadline ? `<div>Deadline: ${escapeHTML(dateLabel(group.deadline))}</div>` : ""}
        ${payloadMarkup(group)}<span class="hint">Group ${escapeHTML(group.group_event_id)}</span>`;
      return row;
    });
    renderTargets("result-records", detail.result_records, record => {
      const row = document.createElement("li");
      row.className = "result-record-item";
      row.dataset.recordId = record.record_id;
      row.dataset.version = record.version;
      row.innerHTML = `<strong class="target-state">Lifecycle: ${escapeHTML(record.lifecycle || "unknown")}</strong>
        ${record.kind ? `<div>Kind: ${escapeHTML(record.kind)}</div>` : ""}
        ${payloadMarkup(record)}<span class="hint">Record ${escapeHTML(record.record_id)} · version ${escapeHTML(record.version)}</span>`;
      return row;
    });
    renderTargets("run-receipts", detail.run_receipts, receipt => {
      const row = document.createElement("li");
      row.className = "run-receipt-item";
      row.dataset.targetId = receipt.target_id;
      row.innerHTML = `<strong class="receipt-status">Receipt status: ${escapeHTML(receipt.status || "unknown")}</strong>
        <div>Wake state: ${escapeHTML(receipt.wake_state || "not recorded")}</div>
        <div class="assessment-outcome">Assessment: ${escapeHTML(receipt.assessment_outcome || "not recorded")}</div>
        ${payloadMarkup(receipt)}<span class="hint">Receipt ${escapeHTML(receipt.receipt_id || receipt.target_id)}</span>`;
      return row;
    });
  }

  function payloadMarkup(target) {
    const label = target.payload_available === true ? "Payload available" : target.payload_available === false ? "Payload unavailable" : "Payload status unknown";
    return `<strong class="payload-status">${label}</strong>${target.payload_status ? `<span class="hint">Source: ${escapeHTML(target.payload_status)}</span>` : ""}`;
  }

  function renderTargets(name, targets, render) {
    byID(name + "-section").hidden = !(targets || []).length;
    byID("modal-" + name).replaceChildren(...(targets || []).map(render));
  }

  async function showDetail(itemUUID) {
    const project = currentProject;
    const sequence = ++detailRequest;
    try {
      const detail = await apiFetch("/api/work/item?" + new URLSearchParams({ project, item: itemUUID }));
      if (sequence !== detailRequest || project !== currentProject) return;
      const newlyOpened = byID("detail-modal").hidden;
      const drafts = new Map();
      if (!newlyOpened && currentItemUUID === itemUUID) {
        for (const input of document.querySelectorAll(".edit-progress-input:not([hidden])")) {
          const row = input.closest(".annot-item");
          drafts.set(row.dataset.annotationId, { value: input.value, revision: input.dataset.expectedRevision,
            focused: document.activeElement === input, start: input.selectionStart, end: input.selectionEnd });
        }
      }
      if (newlyOpened) previousFocus = document.activeElement;
      currentItemUUID = itemUUID;
      byID("modal-title").textContent = detail.item.title;
      byID("modal-meta").innerHTML = `<div><strong>Identifier:</strong> ${escapeHTML(detail.item.identifier)}</div>
        <div><strong>UUID:</strong> ${escapeHTML(detail.item.plane_item_uuid)}</div>
        <div><strong>Plane state:</strong> ${escapeHTML(detail.item.state_name || detail.item.state_group || "Unavailable")}</div>
        <div class="item-assignees"><strong>Plane assignees:</strong> ${escapeHTML((detail.item.assignee_names || []).join(", ") || "None returned")}</div>`;
      if (detail.item.source_url) {
        const url = new URL(detail.item.source_url);
        if (url.protocol === "https:" || url.protocol === "http:") {
          const link = document.createElement("a");
          link.href = url.href;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = "Open in Plane";
          byID("modal-meta").append(link);
        }
      }
      renderLinkedState(detail);
      const associationList = byID("modal-associations");
      associationList.replaceChildren();
      for (const association of detail.associations || []) {
        const row = document.createElement("li");
        row.className = "assoc-item";
        row.dataset.associationId = association.association_id;
        row.innerHTML = `<div>${escapeHTML(association.target_type)}: ${escapeHTML(association.target_id)}</div>
          <span class="hint">${escapeHTML(association.rationale)} · ${escapeHTML(association.state)}</span>`;
        if (association.state === "active") {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "remove-assoc-btn";
          button.textContent = "Remove association";
          button.disabled = !Number.isSafeInteger(association.revision) || association.revision < 1;
          button.addEventListener("click", () => mutate("unassociate", { association_id: association.association_id, expected_revision: association.revision }));
          row.append(button);
        }
        associationList.append(row);
      }
      const annotationList = byID("modal-annotations");
      annotationList.replaceChildren();
      for (const annotation of detail.annotations || []) {
        const row = document.createElement("li");
        row.className = "annot-item";
        row.dataset.annotationId = annotation.annotation_id;
        row.dataset.kind = annotation.kind;
        row.dataset.revision = annotation.revision;
        row.dataset.state = annotation.state;
        row.innerHTML = `<strong>${escapeHTML(annotation.kind)}</strong> <span class="annot-body">${escapeHTML(annotation.body)}</span>
          <span class="hint">${escapeHTML(annotation.state)} · revision ${escapeHTML(annotation.revision)} · ${escapeHTML(annotation.author)}</span>`;
        const guarded = Number.isSafeInteger(annotation.revision) && annotation.revision > 0;
        if (annotation.kind === "progress" && annotation.state === "active") {
          const actions = document.createElement("div");
          actions.className = "annotation-actions";
          actions.innerHTML = '<button type="button" class="edit-progress-btn">Edit progress</button><input class="edit-progress-input" aria-label="Revised progress" hidden><button type="button" class="save-progress-btn" hidden>Save progress</button>';
          const edit = actions.querySelector(".edit-progress-btn");
          const input = actions.querySelector("input");
          const save = actions.querySelector(".save-progress-btn");
          const draft = drafts.get(annotation.annotation_id);
          input.value = draft ? draft.value : annotation.body;
          input.dataset.expectedRevision = draft ? draft.revision : String(annotation.revision);
          edit.dataset.guardInvalid = String(!guarded);
          save.dataset.guardInvalid = String(!guarded || Boolean(draft && draft.revision !== String(annotation.revision)));
          if (draft) {
            edit.hidden = true;
            input.hidden = save.hidden = false;
            if (save.dataset.guardInvalid === "true") {
              const warning = document.createElement("p");
              warning.className = "hint";
              warning.textContent = "This progress note changed. Your draft is preserved; inspect the current note before starting a new edit.";
              actions.append(warning);
            }
          }
          edit.addEventListener("click", () => {
            edit.hidden = true;
            input.hidden = save.hidden = false;
            input.focus();
          });
          save.addEventListener("click", () => {
            if (input.value.trim()) mutate("progress/edit", { annotation_id: annotation.annotation_id, text: input.value.trim(), expected_revision: Number(input.dataset.expectedRevision) }, () => { input.hidden = true; });
          });
          row.append(actions);
        } else if (annotation.kind === "blocker" && annotation.state === "open") {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "resolve-blocker-btn";
          button.textContent = "Resolve blocker";
          button.dataset.guardInvalid = String(!guarded);
          button.addEventListener("click", () => mutate("blocker/resolve", { annotation_id: annotation.annotation_id, expected_revision: annotation.revision }));
          row.append(button);
        }
        annotationList.append(row);
      }
      for (const [id, draft] of drafts) {
        if ([...annotationList.querySelectorAll(".edit-progress-input")].some(input => input.closest(".annot-item").dataset.annotationId === id)) continue;
        const row = [...annotationList.children].find(node => node.dataset.annotationId === id) || document.createElement("li");
        row.className = "annot-item";
        row.dataset.annotationId = id;
        const label = document.createElement("label");
        label.textContent = "The selected note is no longer editable. Your draft is preserved for copying.";
        const input = document.createElement("input");
        input.className = "edit-progress-input";
        input.dataset.expectedRevision = draft.revision;
        input.value = draft.value;
        input.readOnly = true;
        label.append(input);
        row.append(label);
        if (!row.isConnected) annotationList.append(row);
      }
      updateWorkControls();
      byID("detail-modal").hidden = false;
      syncModalState();
      if (newlyOpened) byID("modal-close").focus();
      for (const input of document.querySelectorAll(".edit-progress-input:not([hidden])")) {
        const draft = drafts.get(input.closest(".annot-item").dataset.annotationId);
        if (draft?.focused) { input.focus(); input.setSelectionRange(draft.start, draft.end); }
      }
    } catch (error) {
      message(byID("detail-modal").hidden ? "view-error" : "detail-error", "Could not load item: " + error.message);
    }
  }

  async function write(path, payload, success) {
    if (writing) return;
    writing = true;
    const key = JSON.stringify([path, payload]);
    if (!pendingWrites.has(key)) pendingWrites.set(key, crypto.randomUUID());
    const disabled = [...document.querySelectorAll("button, input, select, textarea")].map(node => [node, node.disabled]);
    for (const [node] of disabled) node.disabled = true;
    let controlsRestored = false;
    const restoreControls = () => {
      if (controlsRestored) return;
      for (const [node, wasDisabled] of disabled) if (node.isConnected) node.disabled = wasDisabled;
      controlsRestored = true;
    };
    const errorTarget = byID("detail-modal").hidden ? "view-error" : "detail-error";
    message(errorTarget);
    try {
      await apiFetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...payload, request_id: pendingWrites.get(key) }) });
      pendingWrites.delete(key);
      // Restore before new reads update derived pagination and action states.
      restoreControls();
      await success();
    } catch (error) {
      message(errorTarget, "Change not confirmed: " + error.message + ". Check the item before retrying.");
    } finally {
      restoreControls();
      writing = false;
      updateWorkControls();
    }
  }

  function mutate(operation, values, success = () => {}) {
    const project = currentProject;
    const item = currentItemUUID;
    if (!project || !item) return;
    return write("/api/work/" + operation, { project, item_uuid: item, ...values }, async () => {
      success();
      if (currentProject === project && currentItemUUID === item) await showDetail(item);
      await loadBoard();
    });
  }

  byID("modal-close").addEventListener("click", closeDetail);
  document.addEventListener("keydown", event => {
    const modal = !byID("login-modal").hidden ? byID("login-modal") : !byID("detail-modal").hidden ? byID("detail-modal") : null;
    if (!modal) return;
    if (event.key === "Escape" && modal.id === "detail-modal") {
      event.preventDefault();
      if (!writing) closeDetail();
    }
    if (event.key === "Tab") {
      const nodes = [...modal.querySelectorAll("button, a, input, select, textarea")].filter(node => !node.disabled && node.getClientRects().length);
      const first = nodes[0], last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  });
  byID("project-select").addEventListener("change", event => { currentProject = event.target.value; currentPage = 1; closeDetail(); loadBoard(); });
  for (const id of ["filter-state-group", "filter-blocker-only"]) byID(id).addEventListener("change", () => { currentPage = 1; loadBoard(); });
  byID("page-prev-btn").addEventListener("click", () => { if (currentPage > 1) { currentPage--; loadBoard(); } });
  byID("page-next-btn").addEventListener("click", () => { if (currentPage * pageSize < totalItems) { currentPage++; loadBoard(); } });
  byID("refresh-btn").addEventListener("click", async () => {
    if (!currentProject) return;
    await write("/api/work/refresh?" + new URLSearchParams({ project: currentProject }), {}, () => {});
    await loadBoard(true);
  });
  for (const [id, operation] of [["add-progress-btn", "progress/add"], ["add-blocker-btn", "blocker/add"]]) {
    byID(id).addEventListener("click", () => {
      const input = byID("annotation-input");
      if (input.value.trim()) mutate(operation, { text: input.value.trim() }, () => { input.value = ""; });
    });
  }
  byID("assoc-target-type").addEventListener("change", event => {
    byID("assoc-target-help").textContent = ({ session: "Agent UUID:execution UUID", event: "Exact event UUID", response_group: "Exact response group UUID", run_receipt: "Exact run receipt ID", result_record: "Record UUID:version" })[event.target.value];
  });
  byID("add-assoc-btn").addEventListener("click", () => {
    const target = byID("assoc-target-id"), rationale = byID("assoc-rationale");
    if (target.value.trim()) mutate("associate", { target_type: byID("assoc-target-type").value, target_id: target.value.trim(), rationale: rationale.value.trim() }, () => { target.value = rationale.value = ""; });
  });
  byID("login-form").addEventListener("submit", async event => {
    event.preventDefault();
    const input = byID("launch-code-input");
    if (!input.value.trim()) return;
    const button = event.target.querySelector("button");
    button.disabled = true;
    try {
      const data = await apiFetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code: input.value.trim() }) });
      input.value = "";
      csrfToken = data.csrf_token || csrfToken;
      byID("login-modal").hidden = true;
      message("login-error");
      syncModalState();
      await loadProjects();
      byID("project-select").focus();
    } catch (error) {
      message("login-error", "Login failed: " + error.message);
    } finally { button.disabled = false; }
  });
  async function initialize() {
    try {
      const session = await apiFetch("/api/auth/session");
      if (session.authenticated) {
        csrfToken = session.csrf_token || csrfToken;
        await loadProjects();
      } else showLogin();
    } catch (error) {
      showLogin();
      message("login-error", "Could not check the local session: " + error.message);
    }
  }
  initialize();
})();
