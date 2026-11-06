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
  let artifactStatus = "";
  let artifactItemKey = "";
  let artifactListRequest = 0;
  let artifactHistoryRequest = 0;
  let artifactHistoryLink = "";
  let artifactHistoryCursor = "";
  let artifactHistoryPage = 1;
  let artifactRequest = 0;
  let artifactRevisionID = "";
  let artifactRevision = null;
  let artifactImpactCursor = "";
  let artifactImpactPage = 1;
  let artifactReturnItem = null;
  const pendingWrites = new Map();
  const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

  function validUUID(value) {
    return uuidPattern.test(value) && value !== "00000000-0000-0000-0000-000000000000";
  }

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
    artifactListRequest++;
    artifactHistoryRequest++;
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
    updateArtifactControls();
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
    updateArtifactControls();
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
      const itemKey = JSON.stringify([project, itemUUID]);
      if (artifactItemKey !== itemKey) {
        artifactItemKey = itemKey;
        byID("artifact-link-form").reset();
        byID("artifact-register-form").reset();
        byID("artifact-include-removed").checked = false;
        byID("artifact-register-details").open = false;
        updateArtifactKind();
        message("item-artifact-notice");
      }
      clearArtifactHistory();
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
      await loadItemArtifacts();
    } catch (error) {
      message(byID("detail-modal").hidden ? "view-error" : "detail-error", "Could not load item: " + error.message);
    }
  }

  async function write(path, payload, success, errorTarget = byID("detail-modal").hidden ? "view-error" : "detail-error") {
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
    message(errorTarget);
    try {
      const result = await apiFetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...payload, request_id: pendingWrites.get(key) }) });
      // Restore before new reads update derived pagination and action states.
      restoreControls();
      await success(result);
      pendingWrites.delete(key);
    } catch (error) {
      const visibleTarget = !byID("login-modal").hidden ? "login-error" : !byID("detail-modal").hidden ? "detail-error"
        : !byID("artifact-page").hidden ? "artifact-error" : "view-error";
      message(byID(errorTarget).parentElement.closest("[hidden]") ? visibleTarget : errorTarget,
        "Change not confirmed: " + error.message + ". Inspect the current state before retrying.");
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

  function artifactAdmissionLabel() {
    return artifactStatus ? `Artifact admission: ${artifactStatus}.`
      + (artifactStatus === "active" ? "" : " Registration and new links are disabled; retained metadata and maintenance remain available.")
      : "Artifact admission could not be checked. Registration and new links are unavailable.";
  }

  function updateArtifactControls() {
    byID("artifact-register-submit").disabled = artifactStatus !== "active";
    byID("artifact-link-submit").disabled = artifactStatus !== "active" || currentProjectStatus !== "active";
    message("artifact-admission", artifactAdmissionLabel() + (currentProjectStatus && currentProjectStatus !== "active"
      ? ` Project admission: ${currentProjectStatus}; new links are disabled.` : ""));
    message("artifact-page-admission", artifactAdmissionLabel());
  }

  async function readArtifactStatus() {
    try {
      const status = await apiFetch("/api/work/artifact/status");
      if (status.feature_name !== "work-artifacts/1" || !["active", "disabled", "retired"].includes(status.status)) {
        throw new Error("Unexpected artifact status response");
      }
      artifactStatus = status.status;
    } catch (error) {
      artifactStatus = "";
      updateArtifactControls();
      throw error;
    }
    updateArtifactControls();
  }

  function artifactSummary(revision) {
    if (!revision) return "Revision metadata not returned";
    if (revision.unavailable) return "Metadata unavailable";
    const artifact = revision.artifact || {};
    return artifact.display_name || ({ git: revision.git_commit_oid, report: revision.revision_label,
      ci: `${artifact.ci_provider || "CI"} run ${artifact.ci_run_id || "unknown"}, attempt ${revision.ci_attempt ?? "unknown"}`,
      cairn_result: `${artifact.cairn_record_id || "Record"}, version ${revision.cairn_version ?? "unknown"}`
    })[artifact.kind] || revision.revision_id;
  }

  function artifactAvailability(revision) {
    if (!revision) return "Availability not returned";
    return revision.unavailable ? `Metadata unavailable${revision.unavailable_reason ? ": " + revision.unavailable_reason : ""}` : "Metadata retained";
  }

  function actionButton(label, className, action) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = label;
    button.addEventListener("click", action);
    return button;
  }

  async function loadItemArtifacts() {
    const project = currentProject, item = currentItemUUID, sequence = ++artifactListRequest;
    if (!project || !item) return;
    byID("modal-artifacts").replaceChildren();
    byID("item-artifacts-empty").hidden = true;
    message("item-artifact-error");
    const query = new URLSearchParams({ project, item });
    if (byID("artifact-include-removed").checked) query.set("include_removed", "1");
    try {
      await readArtifactStatus();
      const data = await apiFetch("/api/work/artifact/list?" + query);
      if (sequence !== artifactListRequest || currentProject !== project || currentItemUUID !== item) return;
      if (!Array.isArray(data.artifacts)) throw new Error("Artifact list was not returned");
      byID("item-artifacts-empty").hidden = data.artifacts.length !== 0;
      for (const link of data.artifacts) {
        if (link.plane_item_uuid !== item || !validUUID(link.link_id) || !validUUID(link.revision_id)) {
          throw new Error("Artifact link identity did not match this work item");
        }
        const row = document.createElement("li");
        row.className = "artifact-link-item";
        row.dataset.linkId = link.link_id;
        row.dataset.revisionId = link.revision_id;
        row.dataset.revision = link.revision;
        row.dataset.state = link.state;
        row.innerHTML = `<strong>${escapeHTML(artifactSummary(link.artifact_revision))}</strong>
          <div>${escapeHTML(link.role)} · ${escapeHTML(link.state)} · link revision ${escapeHTML(link.revision)}</div>
          <span class="artifact-availability hint">${escapeHTML(artifactAvailability(link.artifact_revision))}</span>
          <span class="hint">Artifact revision ${escapeHTML(link.revision_id)}</span>
          <div class="artifact-rationale">${escapeHTML(link.artifact_revision?.unavailable ? "" : link.rationale)}</div>
          <span class="hint">Linked by ${escapeHTML(link.author)} · ${escapeHTML(dateLabel(link.asserted_at))}</span>
          ${link.removed_at ? `<span class="hint">Removed by ${escapeHTML(link.removed_by)} · ${escapeHTML(dateLabel(link.removed_at))}</span>` : ""}`;
        const actions = document.createElement("div");
        actions.className = "modal-actions";
        actions.append(actionButton("Inspect revision", "inspect-artifact-btn", () => openArtifact(link.revision_id, { project, item })),
          actionButton("Link history", "artifact-history-btn", () => loadArtifactHistory(link.link_id)));
        if (link.state === "active") {
          const remove = actionButton("Remove link", "unlink-artifact-btn", () => unlinkArtifact(link, project, item));
          remove.disabled = !Number.isSafeInteger(link.revision) || link.revision < 1;
          actions.append(remove);
        }
        row.append(actions);
        byID("modal-artifacts").append(row);
      }
      return data.artifacts;
    } catch (error) {
      if (sequence === artifactListRequest) {
        byID("modal-artifacts").replaceChildren();
        byID("item-artifacts-empty").hidden = true;
        message("item-artifact-error", "Could not read artifact links: " + error.message);
      }
    }
  }

  function clearArtifactHistory() {
    artifactHistoryRequest++;
    artifactHistoryLink = "";
    artifactHistoryCursor = "";
    byID("artifact-history-section").hidden = true;
    byID("artifact-history").replaceChildren();
  }

  async function loadArtifactHistory(linkID, cursor = "", page = 1) {
    const project = currentProject, item = currentItemUUID, sequence = ++artifactHistoryRequest;
    if (!project || !item) return;
    artifactHistoryLink = linkID;
    artifactHistoryCursor = "";
    artifactHistoryPage = page;
    byID("artifact-history-section").hidden = false;
    byID("artifact-history").replaceChildren();
    byID("artifact-history-summary").textContent = "Loading link history…";
    byID("artifact-history-first").disabled = byID("artifact-history-next").disabled = true;
    message("artifact-history-error");
    const query = new URLSearchParams({ project, item, link_id: linkID, limit: pageSize });
    if (cursor) query.set("cursor", cursor);
    try {
      const data = await apiFetch("/api/work/artifact/history?" + query);
      if (sequence !== artifactHistoryRequest || item !== currentItemUUID || project !== currentProject) return;
      if (data.link?.link_id !== linkID || data.link?.plane_item_uuid !== item || !Array.isArray(data.history)) {
        throw new Error("History identity did not match this link");
      }
      if (data.more && !data.next_cursor) throw new Error("History continuation was not returned");
      byID("artifact-history-summary").textContent = `${linkID} · current ${data.link.state}, revision ${data.link.revision}. ${artifactAvailability(data.link.artifact_revision)}`;
      for (const entry of data.history) {
        if (entry.link_id !== linkID) throw new Error("History entry did not match this link");
        const row = document.createElement("li");
        row.dataset.historyId = entry.history_id;
        row.innerHTML = `<strong>Revision ${escapeHTML(entry.revision)} · ${escapeHTML(entry.state)} · ${escapeHTML(entry.role)}</strong>
          <div>${escapeHTML(data.link.artifact_revision?.unavailable ? "" : entry.rationale)}</div><span class="hint">${escapeHTML(entry.author)} · ${escapeHTML(dateLabel(entry.created_at))}</span>`;
        byID("artifact-history").append(row);
      }
      artifactHistoryCursor = data.more ? data.next_cursor : "";
      byID("artifact-history-page").textContent = `Page ${page} · ${data.history.length} entries`;
      byID("artifact-history-first").disabled = page === 1;
      byID("artifact-history-next").disabled = !artifactHistoryCursor;
      byID("artifact-history-title").focus();
    } catch (error) {
      if (sequence === artifactHistoryRequest) {
        byID("artifact-history").replaceChildren();
        byID("artifact-history-summary").textContent = linkID;
        byID("artifact-history-page").textContent = "History unavailable";
        byID("artifact-history-first").disabled = false;
        message("artifact-history-error", "Could not read link history: " + error.message + ". Use First page to read current history.");
      }
    }
  }

  async function unlinkArtifact(link, project, item) {
    await write("/api/work/artifact/unlink", { project, item_uuid: item, link_id: link.link_id, expected_revision: link.revision }, async result => {
      if (result.link_id !== link.link_id || result.plane_item_uuid !== item || result.state !== "removed") throw new Error("Removed link was not confirmed");
      if (currentProject !== project || currentItemUUID !== item) return;
      clearArtifactHistory();
      message("item-artifact-notice", "Link removed. Include removed links to inspect its retained history.");
      await loadItemArtifacts();
    }, "item-artifact-error");
  }

  function updateArtifactKind() {
    const kind = byID("artifact-kind").value;
    for (const fieldset of document.querySelectorAll("[data-artifact-kind]")) {
      fieldset.hidden = fieldset.disabled = fieldset.dataset.artifactKind !== kind;
    }
    byID("artifact-label-field").hidden = kind === "cairn_result";
    byID("artifact-revision-label").disabled = kind === "cairn_result";
    byID("artifact-revision-label").required = kind === "report";
    byID("artifact-label-hint").textContent = kind === "report" ? "(required)" : "(optional)";
  }

  function setArtifactRoute(revision = "") {
    const url = new URL(location.href);
    url.searchParams.delete("artifact_revision");
    url.searchParams.delete("view");
    if (revision) url.searchParams.set("artifact_revision", revision);
    else if (!byID("artifact-page").hidden) url.searchParams.set("view", "artifacts");
    if (url.href !== location.href) history.pushState(null, "", url);
  }

  function showArtifactPage() {
    closeDetail();
    byID("work-board").hidden = true;
    byID("artifact-page").hidden = false;
    byID("project-select").hidden = true;
    document.querySelector(".header-right").hidden = true;
    byID("board-view-btn").removeAttribute("aria-current");
    byID("artifact-view-btn").setAttribute("aria-current", "page");
    byID("artifact-return-item").hidden = !artifactReturnItem;
  }

  function showBoard(updateURL = true) {
    artifactRequest++;
    byID("artifact-page").hidden = true;
    byID("work-board").hidden = false;
    byID("project-select").hidden = false;
    document.querySelector(".header-right").hidden = false;
    byID("artifact-view-btn").removeAttribute("aria-current");
    byID("board-view-btn").setAttribute("aria-current", "page");
    if (updateURL) setArtifactRoute();
    byID("board-view-btn").focus();
  }

  function appendArtifactField(list, label, value) {
    if (value === undefined || value === null || value === "") return;
    const term = document.createElement("dt"), detail = document.createElement("dd");
    term.textContent = label;
    detail.textContent = String(value);
    list.append(term, detail);
  }

  function renderArtifactRevision(revision) {
    if (revision.revision_id !== artifactRevisionID || !validUUID(revision.artifact_id)) throw new Error("Artifact revision identity did not match the request");
    artifactRevision = revision;
    const artifact = revision.artifact || {};
    const container = byID("artifact-revision-details");
    container.replaceChildren();
    const heading = document.createElement("p");
    heading.className = "artifact-availability";
    heading.textContent = artifactAvailability(revision);
    const list = document.createElement("dl");
    list.className = "artifact-identity";
    appendArtifactField(list, "Artifact revision UUID", revision.revision_id);
    appendArtifactField(list, "Artifact UUID", revision.artifact_id);
    appendArtifactField(list, "Kind", artifact.kind);
    appendArtifactField(list, "Registered by", revision.created_by);
    appendArtifactField(list, "Registered at", dateLabel(revision.created_at));
    container.append(heading, list);
    if (!revision.unavailable) {
      for (const [label, value] of [
        ["Display name", artifact.display_name], ["Revision label", revision.revision_label],
        ["Repository", artifact.git_repo || artifact.ci_repo], ["Full commit object ID", revision.git_commit_oid],
        ["Object format", revision.git_object_format], ["Branch hint (not identity)", revision.git_branch_hint],
        ["Report locator", artifact.report_locator], ["Selected digest", revision.report_digest],
        ["Recorded report provenance", revision.report_provenance], ["CI provider", artifact.ci_provider],
        ["CI run ID", artifact.ci_run_id], ["CI attempt", revision.ci_attempt], ["Reported CI status", revision.ci_status],
        ["CI observation time", revision.ci_reported_at ? dateLabel(revision.ci_reported_at) : ""],
        ["Cairn record UUID", artifact.cairn_record_id], ["Exact record version", revision.cairn_version]
      ]) appendArtifactField(list, label, value);
      if (artifact.kind === "report" || artifact.kind === "ci") {
        const note = document.createElement("p");
        note.className = "hint";
        note.textContent = artifact.kind === "report" ? "This view does not fetch or verify report bytes." + (revision.report_mutable ? " The report locator may change." : "")
          : "CI status is a selected observation. It does not mean Cairn executed or accepted the run.";
        container.append(note);
      }
      const locator = artifact.kind === "report" ? artifact.report_locator : artifact.git_repo || artifact.ci_repo;
      if (locator) {
        try {
          const url = new URL(locator);
          if (url.protocol === "https:" && !url.username && !url.password) {
            const link = document.createElement("a");
            link.href = url.href;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.textContent = artifact.kind === "report" ? "Open supplied report locator" : "Open repository";
            container.append(link);
          }
        } catch { /* Non-URL repository identities remain text above. */ }
      }
      if (revision.metadata && Object.keys(revision.metadata).length) {
        const details = document.createElement("details"), summary = document.createElement("summary"), pre = document.createElement("pre");
        summary.textContent = "Selected metadata";
        pre.textContent = JSON.stringify(revision.metadata, null, 2);
        details.append(summary, pre);
        container.append(details);
      }
    }
    byID("artifact-revision-section").hidden = false;
    byID("artifact-purge-btn").disabled = false;
  }

  async function openArtifact(revisionID = "", returnItem = artifactReturnItem, updateURL = true) {
    artifactReturnItem = returnItem;
    artifactRevisionID = revisionID.trim();
    artifactRevision = null;
    artifactImpactCursor = "";
    artifactImpactPage = 1;
    const sequence = ++artifactRequest;
    showArtifactPage();
    byID("artifact-lookup-id").value = artifactRevisionID;
    byID("artifact-revision-section").hidden = byID("artifact-impact-section").hidden = true;
    byID("artifact-revision-details").replaceChildren();
    byID("artifact-impacts").replaceChildren();
    byID("artifact-purge-btn").disabled = true;
    message("artifact-error");
    message("artifact-notice");
    if (updateURL) setArtifactRoute(artifactRevisionID);
    byID("artifact-page-title").focus();
    try { await readArtifactStatus(); }
    catch (error) { if (sequence === artifactRequest) message("artifact-error", "Could not read artifact admission: " + error.message); }
    if (!artifactRevisionID || sequence !== artifactRequest) return;
    if (!validUUID(artifactRevisionID)) { message("artifact-error", "Enter a nonzero artifact revision UUID."); return; }
    try {
      const revision = await apiFetch("/api/work/artifact/show?" + new URLSearchParams({ revision: artifactRevisionID }));
      if (sequence !== artifactRequest) return;
      renderArtifactRevision(revision);
      await loadArtifactImpact();
    } catch (error) {
      if (sequence === artifactRequest) message("artifact-error", "Could not inspect revision: " + error.message);
    }
  }

  async function loadArtifactImpact(cursor = "", page = 1) {
    const revisionID = artifactRevisionID, sequence = ++artifactRequest;
    artifactImpactCursor = "";
    artifactImpactPage = page;
    byID("artifact-impact-section").hidden = false;
    byID("artifact-impacts").replaceChildren();
    byID("artifact-impact-empty").hidden = true;
    byID("artifact-impact-page").textContent = "Loading…";
    byID("artifact-impact-first").disabled = byID("artifact-impact-next").disabled = true;
    const query = new URLSearchParams({ revision: revisionID, limit: pageSize });
    if (cursor) query.set("cursor", cursor);
    try {
      const data = await apiFetch("/api/work/artifact/impact?" + query);
      if (sequence !== artifactRequest) return;
      if (!Array.isArray(data.impacts) || data.more && !data.next_cursor) throw new Error("Impact page or continuation was not returned");
      renderArtifactRevision(data.revision);
      for (const impact of data.impacts) {
        const row = document.createElement("li");
        row.className = "artifact-impact-item";
        row.dataset.linkId = impact.link_id;
        row.dataset.itemUuid = impact.plane_item_uuid;
        row.innerHTML = `<strong>${escapeHTML(impact.item_identifier || impact.plane_item_uuid)}</strong>
          <div>${escapeHTML(impact.item_title)}</div><div>${escapeHTML(impact.project_slug)} · ${escapeHTML(impact.role)} · ${escapeHTML(impact.link_state)}</div>
          <span class="hint">Plane state: ${escapeHTML(impact.state_name || impact.state_group || "Unavailable")}</span>
          <span class="hint">Source updated: ${escapeHTML(dateLabel(impact.updated_at_source))}</span>
          <span class="hint">Linked by ${escapeHTML(impact.link_author)} · ${escapeHTML(dateLabel(impact.link_asserted_at))}</span>`;
        for (const [label, values] of [["Linked executions", impact.linked_sessions], ["Open blockers", impact.open_blockers]]) {
          if (values?.length) {
            const title = document.createElement("p"), list = document.createElement("ul");
            title.textContent = label;
            for (const value of values) { const entry = document.createElement("li"); entry.textContent = value; list.append(entry); }
            row.append(title, list);
          }
        }
        row.append(actionButton("Open work item", "impact-open-item", () => openImpactItem(impact.project_slug, impact.plane_item_uuid)));
        byID("artifact-impacts").append(row);
      }
      artifactImpactCursor = data.more ? data.next_cursor : "";
      byID("artifact-impact-empty").hidden = data.impacts.length !== 0;
      byID("artifact-impact-page").textContent = `Page ${page} · ${data.impacts.length} links`;
      byID("artifact-impact-first").disabled = page === 1;
      byID("artifact-impact-next").disabled = !artifactImpactCursor;
      message("artifact-error");
    } catch (error) {
      if (sequence === artifactRequest) {
        artifactRevision = null;
        byID("artifact-revision-details").replaceChildren();
        byID("artifact-revision-section").hidden = true;
        byID("artifact-purge-btn").disabled = true;
        byID("artifact-impacts").replaceChildren();
        byID("artifact-impact-page").textContent = "Impact unavailable";
        byID("artifact-impact-first").disabled = false;
        message("artifact-error", "Could not read reverse impact: " + error.message + ". Use First page to read current links.");
      }
    }
  }

  async function openImpactItem(project, item) {
    if (![...byID("project-select").options].some(option => option.value === project) || !validUUID(item)) {
      message("artifact-error", "The linked project or item is not available in this view.");
      return;
    }
    currentProject = project;
    currentPage = 1;
    byID("project-select").value = project;
    byID("filter-state-group").value = "";
    byID("filter-blocker-only").checked = false;
    showBoard();
    await loadBoard();
    await showDetail(item);
  }

  function loadRoute() {
    const query = new URLSearchParams(location.search);
    if (query.has("artifact_revision") || query.get("view") === "artifacts") return openArtifact(query.get("artifact_revision") || "", null, false);
    showBoard(false);
  }

  byID("board-view-btn").addEventListener("click", () => showBoard());
  byID("artifact-view-btn").addEventListener("click", () => openArtifact("", null));
  byID("artifact-lookup-form").addEventListener("submit", event => {
    event.preventDefault();
    openArtifact(byID("artifact-lookup-id").value);
  });
  byID("artifact-return-item").addEventListener("click", () => {
    if (artifactReturnItem) openImpactItem(artifactReturnItem.project, artifactReturnItem.item);
  });
  byID("artifact-include-removed").addEventListener("change", () => { clearArtifactHistory(); loadItemArtifacts(); });
  byID("artifact-kind").addEventListener("change", updateArtifactKind);
  byID("artifact-history-close").addEventListener("click", () => {
    const linkID = artifactHistoryLink;
    clearArtifactHistory();
    [...byID("modal-artifacts").children].find(row => row.dataset.linkId === linkID)?.querySelector(".artifact-history-btn")?.focus();
  });
  byID("artifact-history-first").addEventListener("click", () => loadArtifactHistory(artifactHistoryLink));
  byID("artifact-history-next").addEventListener("click", () => {
    if (artifactHistoryCursor) loadArtifactHistory(artifactHistoryLink, artifactHistoryCursor, artifactHistoryPage + 1);
  });
  byID("artifact-impact-first").addEventListener("click", () => loadArtifactImpact());
  byID("artifact-impact-next").addEventListener("click", () => {
    if (artifactImpactCursor) loadArtifactImpact(artifactImpactCursor, artifactImpactPage + 1);
  });
  byID("artifact-link-inspect").addEventListener("click", () => {
    const revision = byID("artifact-link-revision").value.trim();
    if (!validUUID(revision)) { message("item-artifact-error", "Enter a nonzero artifact revision UUID."); return; }
    openArtifact(revision, { project: currentProject, item: currentItemUUID });
  });
  byID("artifact-register-form").addEventListener("submit", async event => {
    event.preventDefault();
    const itemKey = artifactItemKey;
    const payload = Object.fromEntries([...new FormData(event.target)].map(([key, value]) => [key, value.trim()]).filter(([, value]) => value !== ""));
    for (const key of ["ci_attempt", "cairn_version"]) {
      if (payload[key] !== undefined) {
        payload[key] = Number(payload[key]);
        if (!Number.isSafeInteger(payload[key]) || payload[key] < 1) { message("item-artifact-error", "Attempt and version must be positive whole numbers."); return; }
      }
    }
    if (payload.cairn_record_id && !validUUID(payload.cairn_record_id)) { message("item-artifact-error", "Enter a nonzero Cairn record UUID."); return; }
    await write("/api/work/artifact/register", payload, async revision => {
      if (!validUUID(revision.revision_id) || !validUUID(revision.artifact_id) || revision.artifact?.kind !== payload.kind) {
        throw new Error("Registered artifact identity was not returned");
      }
      if (itemKey !== artifactItemKey) return;
      byID("artifact-link-revision").value = revision.revision_id;
      message("item-artifact-notice", `Registered revision ${revision.revision_id}. Choose a role and use “Link revision to this item” to create the link.`);
      byID("artifact-register-details").open = false;
      byID("artifact-link-revision").focus();
      await loadItemArtifacts();
    }, "item-artifact-error");
  });
  byID("artifact-link-form").addEventListener("submit", async event => {
    event.preventDefault();
    const project = currentProject, item = currentItemUUID, revisionID = byID("artifact-link-revision").value.trim();
    if (!validUUID(revisionID)) { message("item-artifact-error", "Enter a nonzero artifact revision UUID."); return; }
    const payload = { project, item_uuid: item, revision_id: revisionID, role: byID("artifact-link-role").value, rationale: byID("artifact-link-rationale").value.trim() };
    await write("/api/work/artifact/link", payload, async link => {
      if (!validUUID(link.link_id) || link.plane_item_uuid !== item || link.revision_id !== revisionID || link.role !== payload.role || link.state !== "active") {
        throw new Error("Exact artifact link was not confirmed");
      }
      if (currentProject !== project || currentItemUUID !== item) return;
      clearArtifactHistory();
      const links = await loadItemArtifacts();
      const retained = links?.find(row => row.link_id === link.link_id);
      if (!retained || retained.revision_id !== revisionID || retained.revision !== link.revision || retained.state !== "active" || (retained.rationale || "") !== (link.rationale || "")) {
        throw new Error("Saved link could not be matched to its current read; inspect the item before retrying");
      }
      message("item-artifact-notice", (link.rationale || "") === payload.rationale ? `Revision linked as ${link.role}.`
        : "An existing active link was returned. Its retained reason is shown in the link list; the new reason was not saved.");
    }, "item-artifact-error");
  });
  byID("artifact-purge-btn").addEventListener("click", async () => {
    const revision = artifactRevision;
    if (!revision || !confirm(`Purge all local metadata for artifact ${revision.artifact_id}? This affects every registered revision of this artifact. External files and services are unchanged.`)) return;
    const sequence = artifactRequest, requestedRevision = artifactRevisionID;
    await write("/api/work/artifact/purge", { artifact_id: revision.artifact_id }, result => {
      if (!result.purged || result.artifact_id !== revision.artifact_id) throw new Error("Purge was not confirmed for this artifact");
      if (sequence !== artifactRequest || requestedRevision !== artifactRevisionID || byID("artifact-page").hidden) return;
      artifactRequest++;
      artifactRevision = null;
      byID("artifact-revision-section").hidden = byID("artifact-impact-section").hidden = true;
      byID("artifact-revision-details").replaceChildren();
      byID("artifact-impacts").replaceChildren();
      byID("artifact-purge-btn").disabled = true;
      message("artifact-notice", `Purged local metadata for artifact ${revision.artifact_id}.`);
    }, "artifact-error");
  });
  window.addEventListener("popstate", loadRoute);

  byID("modal-close").addEventListener("click", closeDetail);
  document.addEventListener("keydown", event => {
    const modal = !byID("login-modal").hidden ? byID("login-modal") : !byID("detail-modal").hidden ? byID("detail-modal") : null;
    if (!modal) return;
    if (event.key === "Escape" && modal.id === "detail-modal") {
      event.preventDefault();
      if (!writing) closeDetail();
    }
    if (event.key === "Tab") {
      const nodes = [...modal.querySelectorAll("button, a, input, select, textarea, summary")].filter(node => !node.disabled && node.getClientRects().length);
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
      await loadRoute();
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
        await loadRoute();
      } else showLogin();
    } catch (error) {
      showLogin();
      message("login-error", "Could not check the local session: " + error.message);
    }
  }
  initialize();
})();
