let selectedRunId = null;
let dashboardTimeline = [];

async function requestJson(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json();
  return { ok: response.ok, status: response.status, payload };
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderRunList(runs) {
  if (!runs.length) {
    return '<p class="empty">No runs recorded.</p>';
  }
  return runs.map((run) => {
    const runId = escapeHtml(run.run_id);
    const decision = escapeHtml(run.final_decision || "unknown");
    const digest = escapeHtml((run.evidence_digest || "").slice(0, 12));
    return `<button class="run-row" data-run-id="${runId}"><span>${runId}</span><strong>${decision}</strong><code>${digest}</code></button>`;
  }).join("");
}

function renderEvidenceDetail(payload) {
  const evidence = payload.evidence || {};
  const agents = evidence.agents || {};
  return `
    <h2>${escapeHtml(payload.run_id || evidence.run_id)}</h2>
    <dl class="evidence-grid">
      <dt>Decision</dt><dd>${escapeHtml(evidence.final_decision || "unknown")}</dd>
      <dt>Builder</dt><dd>${escapeHtml(agents.builder || "")}</dd>
      <dt>Verifier</dt><dd>${escapeHtml(agents.verifier || "")}</dd>
      <dt>Digest</dt><dd><code>${escapeHtml(payload.evidence_digest || "")}</code></dd>
    </dl>`;
}

function renderApprovalResult(payload) {
  const approval = payload.approval || {};
  const decision = approval.decision || payload.decision || {};
  const allowed = Boolean(decision.allow);
  const reasons = decision.reasons || decision.denials || [];
  const items = reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("");
  return `<section class="approval-result ${allowed ? "allowed" : "denied"}"><h3>${allowed ? "Approved" : "Denied"}</h3><ul>${items}</ul></section>`;
}

function renderSummaryCards(title, counts) {
  const entries = Object.entries(counts || {}).sort();
  const cells = entries.length
    ? entries.map(([name, value]) => `<span><strong>${escapeHtml(value)}</strong>${escapeHtml(name)}</span>`).join("")
    : "<span><strong>0</strong>none</span>";
  return `<div class="summary-card"><h2>${escapeHtml(title)}</h2>${cells}</div>`;
}

function renderActivityStream(activity) {
  if (!activity.length) {
    return '<p class="empty">No activity recorded.</p>';
  }
  return activity.map((item) => `
    <article class="activity-row">
      <strong>${escapeHtml(item.summary)}</strong>
      <span>${escapeHtml(item.client)} · ${escapeHtml(item.actor)} · ${escapeHtml(item.role)}</span>
      <code>${escapeHtml(item.kind)} / ${escapeHtml(item.status)}</code>
    </article>`).join("");
}

function renderEventTimeline(events) {
  if (!events.length) {
    return '<p class="empty">No events match the current filters.</p>';
  }
  return `<div class="timeline-list">${events.map((event) => `
    <article class="timeline-row ${escapeHtml(event.kind)} ${escapeHtml(event.status)}" data-event-kind="${escapeHtml(event.kind)}" data-event-status="${escapeHtml(event.status)}">
      <div>
        <strong>${escapeHtml(event.summary)}</strong>
        <span>${escapeHtml(event.created_at || event.updated_at || "")}</span>
      </div>
      <code>${escapeHtml(event.kind)} / ${escapeHtml(event.status)}</code>
      <small>${escapeHtml(event.run_id || "no-run")} / ${escapeHtml(event.client || event.client_id || "")} / ${escapeHtml(event.role || "")}</small>
    </article>`).join("")}</div>`;
}

function uniqueTimelineValues(events, field) {
  return [...new Set(events.map((event) => event[field]).filter((value) => value !== null && value !== undefined && value !== ""))].sort();
}

function populateTimelineFilter(id, values) {
  const select = document.getElementById(id);
  const current = select.value;
  select.innerHTML = '<option value="">All</option>' + values.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
  if (values.includes(current)) {
    select.value = current;
  }
}

function updateTimelineFilters(events) {
  populateTimelineFilter("timeline-filter-run", uniqueTimelineValues(events, "run_id"));
  populateTimelineFilter("timeline-filter-client", uniqueTimelineValues(events, "client"));
  populateTimelineFilter("timeline-filter-status", uniqueTimelineValues(events, "status"));
  populateTimelineFilter("timeline-filter-kind", uniqueTimelineValues(events, "kind"));
  populateTimelineFilter("timeline-filter-role", uniqueTimelineValues(events, "role"));
}

function selectedTimelineFilters() {
  return {
    run_id: document.getElementById("timeline-filter-run").value,
    client: document.getElementById("timeline-filter-client").value,
    status: document.getElementById("timeline-filter-status").value,
    kind: document.getElementById("timeline-filter-kind").value,
    role: document.getElementById("timeline-filter-role").value
  };
}

function applyTimelineFilters() {
  const filters = selectedTimelineFilters();
  const filtered = dashboardTimeline.filter((event) => Object.entries(filters).every(([field, value]) => !value || event[field] === value));
  document.getElementById("timeline-count").textContent = `${filtered.length} event${filtered.length === 1 ? "" : "s"}`;
  document.getElementById("event-timeline").innerHTML = renderEventTimeline(filtered);
}

function renderActiveClients(clients) {
  if (!clients.length) {
    return '<p class="empty">No clients registered.</p>';
  }
  return `<div class="client-grid">${clients.map((client) => {
    const status = escapeHtml(client.status || "disconnected");
    const capabilities = (client.supported_capabilities || []).map(escapeHtml).join(", ");
    const heartbeatAge = client.heartbeat_age_seconds === null || client.heartbeat_age_seconds === undefined
      ? "no heartbeat"
      : `${escapeHtml(client.heartbeat_age_seconds)}s ago`;
    return `
      <article class="client-row ${status}" data-client-status="${status}">
        <div>
          <strong>${escapeHtml(client.display_name || client.client_id)}</strong>
          <span>${escapeHtml(client.client_type)} / ${escapeHtml(client.connection_mode)}</span>
        </div>
        <code>${status}</code>
        <span>${heartbeatAge}</span>
        <small>${capabilities || "no capabilities"}</small>
      </article>`;
  }).join("")}</div>`;
}

function renderWorkItems(activity) {
  const open = activity.filter((item) => ["active", "blocked", "needs_human"].includes(item.status));
  if (!open.length) {
    return '<p class="empty">No open work items.</p>';
  }
  return `<ul class="work-items">${open.map((item) => `
    <li><strong>${escapeHtml(item.task_id || item.activity_id)}</strong><span>${escapeHtml(item.summary)}</span><code>${escapeHtml(item.status)}</code></li>`).join("")}</ul>`;
}

function renderTaskBoard(columns) {
  const statuses = ["active", "blocked", "passed", "failed", "needs_human"];
  return `<div class="task-board-grid">${statuses.map((status) => {
    const tasks = (columns && columns[status]) || [];
    const rows = tasks.length
      ? tasks.map((task) => {
        const owner = task.lease_owner || task.client || task.client_id || "unassigned";
        const expiry = task.lease_expires_at || "no expiry";
        return `
          <article class="task-card ${escapeHtml(status)}" data-task-status="${escapeHtml(status)}">
            <strong>${escapeHtml(task.task_id || "task")}</strong>
            <span>${escapeHtml(task.summary || "")}</span>
            <code>${escapeHtml(task.kind || "task")} / ${escapeHtml(task.status || status)}</code>
            <small>Owner ${escapeHtml(owner)} / Expires ${escapeHtml(expiry)}</small>
          </article>`;
      }).join("")
      : '<p class="empty">No tasks.</p>';
    return `<section class="task-column ${escapeHtml(status)}"><h3>${escapeHtml(status)}</h3>${rows}</section>`;
  }).join("")}</div>`;
}

function renderProofStatus(proofs) {
  if (!proofs.length) {
    return '<p class="empty">No proof events recorded.</p>';
  }
  return `<div class="proof-status-list">${proofs.map((proof) => `
    <article class="proof-row ${escapeHtml(proof.status)}" data-proof-status="${escapeHtml(proof.status)}">
      <strong>${escapeHtml(proof.task_id || "proof")}</strong>
      <span>${escapeHtml(proof.summary || "")}</span>
      <code>${escapeHtml(proof.status)} / ${escapeHtml(proof.client || "")}</code>
      <small>Started ${escapeHtml(proof.started_at || "unknown")} / Finished ${escapeHtml(proof.finished_at || "pending")}</small>
      ${proof.recovery_evidence_url ? `<a href="${escapeHtml(proof.recovery_evidence_url)}">Recovery evidence</a>` : ""}
    </article>`).join("")}</div>`;
}

function renderTerminalStatus(rows) {
  if (!rows.length) {
    return '<p class="empty">No terminal status recorded.</p>';
  }
  return `<div class="terminal-list">${rows.map((row) => `
    <article class="terminal-row ${escapeHtml(row.status)}" data-terminal-kind="${escapeHtml(row.kind)}">
      <code>${escapeHtml(row.kind)} / ${escapeHtml(row.status)}</code>
      <span>${escapeHtml(row.summary || "")}</span>
      <small>${escapeHtml(row.created_at || "")} / ${escapeHtml(row.client || "")} / ${escapeHtml(row.role || "")}</small>
    </article>`).join("")}</div>`;
}

function renderPluginStatus(plugins) {
  if (!plugins.length) {
    return '<p class="empty">No plugin status reported.</p>';
  }
  return `<div class="plugin-status-grid">${plugins.map((plugin) => `
    <article class="plugin-status-row ${escapeHtml(plugin.status)}">
      <strong>${escapeHtml(plugin.plugin_id)}</strong>
      <span>${escapeHtml(plugin.target_client)} / local ${escapeHtml(plugin.local_version || plugin.version)}</span>
      <code>${escapeHtml(plugin.status)}</code>
      <small>Publication ${escapeHtml(plugin.publication_readiness || "needs_review")}</small>
      <p>${escapeHtml(plugin.summary)}</p>
      ${plugin.action_required ? `<small>${escapeHtml(plugin.action_required)}</small>` : ""}
    </article>`).join("")}</div>`;
}

function renderKindView(activity, kind) {
  return renderActivityStream(activity.filter((item) => item.kind === kind));
}

async function loadRuns() {
  const { ok, payload } = await requestJson("/runs");
  const list = document.getElementById("run-list");
  list.innerHTML = ok ? renderRunList(payload.runs || []) : '<p class="empty">Unable to load runs.</p>';
}

async function loadEvidence(runId) {
  selectedRunId = runId;
  const { ok, payload } = await requestJson(`/runs/${encodeURIComponent(runId)}/evidence`);
  const detail = document.getElementById("evidence-detail");
  detail.innerHTML = ok ? renderEvidenceDetail(payload) : '<p class="empty">Unable to load evidence.</p>';
}

async function loadDashboard(endpoint = "/dashboard") {
  const { ok, payload } = await requestJson(endpoint);
  const activity = ok ? (payload.activity || []) : [];
  dashboardTimeline = ok ? (payload.event_timeline || activity) : [];
  document.getElementById("status-summary").innerHTML = ok
    ? renderSummaryCards("Status", payload.status_counts) + renderSummaryCards("Clients", payload.client_counts)
    : '<p class="empty">Unable to load dashboard.</p>';
  document.getElementById("active-clients").innerHTML = renderActiveClients(ok ? (payload.active_clients || []) : []);
  document.getElementById("task-board").innerHTML = renderTaskBoard(ok ? (payload.task_board_columns || {}) : {});
  document.getElementById("proof-status").innerHTML = renderProofStatus(ok ? (payload.proof_status || []) : []);
  document.getElementById("terminal-status").innerHTML = renderTerminalStatus(ok ? (payload.terminal_status || []) : []);
  updateTimelineFilters(dashboardTimeline);
  applyTimelineFilters();
  document.getElementById("activity-stream").innerHTML = renderActivityStream(activity);
  document.getElementById("work-items").innerHTML = renderWorkItems(activity);
  document.getElementById("plugin-status").innerHTML = renderPluginStatus(ok ? (payload.plugin_status || []) : []);
  document.getElementById("swarm-view").innerHTML = renderKindView(activity, "swarm");
  document.getElementById("provider-view").innerHTML = renderKindView(activity, "provider");
  document.getElementById("signing-view").innerHTML = renderKindView(activity, "signing");
  document.getElementById("deployment-view").innerHTML = renderKindView(activity, "deployment");
}

async function submitApproval(event) {
  event.preventDefault();
  const result = document.getElementById("approval-result");
  if (!selectedRunId) {
    result.outerHTML = renderApprovalResult({ decision: { allow: false, denials: ["No run selected."] } });
    return;
  }
  const form = new FormData(event.currentTarget);
  const body = {
    run_id: selectedRunId,
    actor: form.get("actor"),
    actor_role: form.get("actor_role"),
    action: "approve_release",
    reason: form.get("reason")
  };
  const { payload } = await requestJson("/actions/approve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  result.outerHTML = renderApprovalResult(payload);
}

async function checkHealth() {
  const status = document.getElementById("health-status");
  const { ok } = await requestJson("/health");
  status.textContent = ok ? "Healthy" : "Unavailable";
}

document.addEventListener("click", (event) => {
  const row = event.target.closest("[data-run-id]");
  if (row) {
    loadEvidence(row.dataset.runId);
  }
});

document.getElementById("refresh-runs").addEventListener("click", loadRuns);
document.getElementById("refresh-dashboard").addEventListener("click", () => loadDashboard());
document.getElementById("replay-dashboard").addEventListener("click", () => {
  const endpoint = selectedRunId ? `/dashboard/replay?run_id=${encodeURIComponent(selectedRunId)}` : "/dashboard/replay";
  loadDashboard(endpoint);
});
document.getElementById("approval-panel").addEventListener("submit", submitApproval);
for (const filter of document.querySelectorAll(".timeline-filters select")) {
  filter.addEventListener("change", applyTimelineFilters);
}
checkHealth();
loadRuns();
loadDashboard();
