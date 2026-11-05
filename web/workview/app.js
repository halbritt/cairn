(function() {
  let currentProject = "";
  let csrfToken = "";

  function escapeHTML(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function showLogin() {
    const modal = document.getElementById("login-modal");
    if (modal) modal.style.display = "flex";
  }

  function hideLogin() {
    const modal = document.getElementById("login-modal");
    if (modal) modal.style.display = "none";
    const err = document.getElementById("login-error");
    if (err) {
      err.style.display = "none";
      err.textContent = "";
    }
  }

  async function apiFetch(url, options = {}) {
    options.headers = options.headers || {};
    if (csrfToken && (options.method === "POST" || options.method === "PUT" || options.method === "DELETE")) {
      options.headers["X-CSRF-Token"] = csrfToken;
    }
    const res = await fetch(url, options);
    if (!res.ok) {
      if (res.status === 401) {
        showLogin();
        throw new Error("Unauthorized");
      }
      const err = await res.json().catch(() => ({ message: res.statusText }));
      throw new Error(err.message || res.statusText);
    }
    const tokenHeader = res.headers.get("X-CSRF-Token");
    if (tokenHeader) csrfToken = tokenHeader;
    return res.json();
  }

  async function loadProjects() {
    try {
      const resp = await apiFetch("/api/work/projects");
      const select = document.getElementById("project-select");
      select.innerHTML = "";
      if (!resp.projects || resp.projects.length === 0) {
        select.innerHTML = '<option value="">No projects registered</option>';
        return;
      }
      resp.projects.forEach(p => {
        const opt = document.createElement("option");
        opt.value = p.local_slug;
        opt.textContent = `${p.local_slug} (${p.plane_origin})`;
        select.appendChild(opt);
      });
      currentProject = resp.projects[0].local_slug;
      select.addEventListener("change", (e) => {
        currentProject = e.target.value;
        loadBoard();
      });
      loadBoard();
    } catch (e) {
      console.error("Failed to load projects:", e);
    }
  }

  async function loadBoard() {
    if (!currentProject) return;
    try {
      const resp = await apiFetch(`/api/work/items?project=${encodeURIComponent(currentProject)}`);
      renderBoard(resp);
    } catch (e) {
      console.error("Failed to load board:", e);
    }
  }

  function renderBoard(data) {
    const alertBox = document.getElementById("auth-alert");
    if (data.project && data.project.auth_rejected) {
      alertBox.style.display = "block";
    } else {
      alertBox.style.display = "none";
    }

    const staleness = document.getElementById("staleness-indicator");
    if (data.project && data.project.last_refreshed_at) {
      const d = new Date(data.project.last_refreshed_at);
      staleness.textContent = `Refreshed: ${d.toLocaleTimeString()}`;
      staleness.className = "badge fresh";
    } else {
      staleness.textContent = "Never refreshed";
      staleness.className = "badge stale";
    }

    ["backlog", "unstarted", "started", "completed", "cancelled"].forEach(g => {
      document.getElementById(`col-${g}`).innerHTML = "";
      document.getElementById(`count-${g}`).textContent = "0";
    });

    const counts = { backlog: 0, unstarted: 0, started: 0, completed: 0, cancelled: 0 };
    (data.items || []).forEach(item => {
      const group = (item.state_group || "backlog").toLowerCase();
      const col = document.getElementById(`col-${group}`) || document.getElementById("col-backlog");
      counts[group] = (counts[group] || 0) + 1;

      const card = document.createElement("div");
      card.className = "work-card";
      card.innerHTML = `
        <div class="card-id">${escapeHTML(item.identifier || item.plane_item_uuid)}</div>
        <div class="card-title">${escapeHTML(item.title)}</div>
        <div class="card-footer">
          <span class="badge">${escapeHTML(item.state_name || item.state_group || "unknown")}</span>
        </div>
      `;
      card.addEventListener("click", () => showDetail(item.plane_item_uuid));
      col.appendChild(card);
    });

    Object.keys(counts).forEach(g => {
      const countEl = document.getElementById(`count-${g}`);
      if (countEl) countEl.textContent = counts[g];
    });
  }

  async function showDetail(itemUUID) {
    try {
      const resp = await apiFetch(`/api/work/item?project=${encodeURIComponent(currentProject)}&item=${itemUUID}`);
      const modal = document.getElementById("detail-modal");
      document.getElementById("modal-title").textContent = resp.item.title;
      document.getElementById("modal-meta").innerHTML = `
        <div><strong>Identifier:</strong> ${escapeHTML(resp.item.identifier)}</div>
        <div><strong>UUID:</strong> ${escapeHTML(resp.item.plane_item_uuid)}</div>
        <div><strong>State:</strong> ${escapeHTML(resp.item.state_name || resp.item.state_group)}</div>
      `;

      const assocList = document.getElementById("modal-associations");
      assocList.innerHTML = "";
      (resp.associations || []).forEach(a => {
        const li = document.createElement("li");
        li.textContent = `[${a.target_type}] ${a.target_id} - ${a.rationale || ""}`;
        assocList.appendChild(li);
      });

      const annotList = document.getElementById("modal-annotations");
      annotList.innerHTML = "";
      (resp.annotations || []).forEach(ann => {
        const li = document.createElement("li");
        li.textContent = `[${ann.kind}] ${ann.text} (by ${ann.author})`;
        annotList.appendChild(li);
      });

      modal.style.display = "flex";
    } catch (e) {
      console.error("Failed to show detail:", e);
    }
  }

  document.getElementById("modal-close").addEventListener("click", () => {
    document.getElementById("detail-modal").style.display = "none";
  });

  document.getElementById("refresh-btn").addEventListener("click", async () => {
    if (!currentProject) return;
    try {
      await apiFetch(`/api/work/refresh?project=${encodeURIComponent(currentProject)}`, { method: "POST" });
      loadBoard();
    } catch (e) {
      alert("Refresh failed: " + e.message);
    }
  });

  const loginForm = document.getElementById("login-form");
  if (loginForm) {
    loginForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const codeInput = document.getElementById("launch-code-input");
      const code = codeInput ? codeInput.value.trim() : "";
      if (!code) return;
      const errEl = document.getElementById("login-error");
      try {
        const res = await fetch("/api/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code: code })
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({ error: res.statusText }));
          throw new Error(body.error || res.statusText);
        }
        const data = await res.json();
        if (data.csrf_token) csrfToken = data.csrf_token;
        hideLogin();
        if (codeInput) codeInput.value = "";
        loadProjects();
      } catch (err) {
        if (errEl) {
          errEl.textContent = "Login failed: " + err.message;
          errEl.style.display = "block";
        }
      }
    });
  }

  window.addEventListener("DOMContentLoaded", loadProjects);
})();
