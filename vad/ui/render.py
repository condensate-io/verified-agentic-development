from __future__ import annotations

import html
from typing import Any


def render_run_list(runs: list[dict[str, Any]]) -> str:
    if not runs:
        return '<p class="empty">No runs recorded.</p>'
    items = []
    for run in runs:
        run_id = html.escape(str(run.get("run_id", "")))
        decision = html.escape(str(run.get("final_decision", "unknown")))
        digest = html.escape(str(run.get("evidence_digest", ""))[:12])
        items.append(
            f'<button class="run-row" data-run-id="{run_id}">'
            f'<span>{run_id}</span><strong>{decision}</strong><code>{digest}</code></button>'
        )
    return "\n".join(items)


def render_evidence_detail(payload: dict[str, Any]) -> str:
    evidence = payload.get("evidence", {})
    run_id = html.escape(str(payload.get("run_id", evidence.get("run_id", ""))))
    decision = html.escape(str(evidence.get("final_decision", "unknown")))
    builder = html.escape(str(evidence.get("agents", {}).get("builder", "")))
    verifier = html.escape(str(evidence.get("agents", {}).get("verifier", "")))
    digest = html.escape(str(payload.get("evidence_digest", "")))
    return (
        f"<h2>{run_id}</h2>"
        f'<dl class="evidence-grid">'
        f"<dt>Decision</dt><dd>{decision}</dd>"
        f"<dt>Builder</dt><dd>{builder}</dd>"
        f"<dt>Verifier</dt><dd>{verifier}</dd>"
        f"<dt>Digest</dt><dd><code>{digest}</code></dd>"
        "</dl>"
    )


def render_approval_result(payload: dict[str, Any]) -> str:
    approval = payload.get("approval", {})
    decision = approval.get("decision", payload.get("decision", {}))
    allowed = bool(decision.get("allow", False))
    label = "Approved" if allowed else "Denied"
    reasons = decision.get("reasons") or decision.get("denials") or []
    items = "".join(f"<li>{html.escape(str(reason))}</li>" for reason in reasons)
    status_class = "allowed" if allowed else "denied"
    return f'<section class="approval-result {status_class}"><h3>{label}</h3><ul>{items}</ul></section>'


def render_dashboard(payload: dict[str, Any]) -> str:
    status = _summary_cards("Status", payload.get("status_counts", {}))
    clients = _summary_cards("Clients", payload.get("client_counts", {}))
    active_clients = render_active_clients(payload.get("active_clients", []))
    task_board = render_task_board(payload.get("task_board_columns", {}))
    proof_status = render_proof_status(payload.get("proof_status", []))
    terminal_status = render_terminal_status(payload.get("terminal_status", []))
    event_timeline = render_event_timeline(payload.get("event_timeline", payload.get("activity", [])))
    activity = render_activity_stream(payload.get("activity", []))
    work_items = render_work_items(payload.get("activity", []))
    plugins = render_plugin_status(payload.get("plugin_status", []))
    return (
        f'<section class="dashboard-summary">{status}{clients}</section>'
        f'<section><h2>Active Clients</h2>{active_clients}</section>'
        f'<section><h2>Task Board</h2>{task_board}</section>'
        f'<section class="dashboard-grid"><div><h2>Proofs</h2>{proof_status}</div><div><h2>Terminal</h2>{terminal_status}</div></section>'
        f'<section><h2>Event Timeline</h2>{event_timeline}</section>'
        f'<section class="dashboard-grid">'
        f'<div><h2>Activity</h2>{activity}</div>'
        f'<div><h2>Work Items</h2>{work_items}</div>'
        "</section>"
        f'<section><h2>Plugins</h2>{plugins}</section>'
    )


def render_task_board(columns: dict[str, list[dict[str, Any]]]) -> str:
    statuses = ["active", "blocked", "passed", "failed", "needs_human"]
    rendered_columns = []
    for status in statuses:
        tasks = columns.get(status, [])
        if tasks:
            rows = []
            for task in tasks:
                owner = task.get("lease_owner") or task.get("client") or task.get("client_id") or "unassigned"
                expiry = task.get("lease_expires_at") or "no expiry"
                rows.append(
                    f'<article class="task-card {html.escape(status)}" data-task-status="{html.escape(status)}">'
                    f'<strong>{html.escape(str(task.get("task_id", "task")))}</strong>'
                    f'<span>{html.escape(str(task.get("summary", "")))}</span>'
                    f'<code>{html.escape(str(task.get("kind", "task")))} / {html.escape(str(task.get("status", status)))}</code>'
                    f'<small>Owner {html.escape(str(owner))} / Expires {html.escape(str(expiry))}</small>'
                    "</article>"
                )
            body = "".join(rows)
        else:
            body = '<p class="empty">No tasks.</p>'
        rendered_columns.append(f'<section class="task-column {html.escape(status)}"><h3>{html.escape(status)}</h3>{body}</section>')
    return f'<div class="task-board-grid">{"".join(rendered_columns)}</div>'


def render_proof_status(proofs: list[dict[str, Any]]) -> str:
    if not proofs:
        return '<p class="empty">No proof events recorded.</p>'
    rows = []
    for proof in proofs:
        status = html.escape(str(proof.get("status", "")))
        recovery = proof.get("recovery_evidence_url")
        recovery_link = f'<a href="{html.escape(str(recovery))}">Recovery evidence</a>' if recovery else ""
        rows.append(
            f'<article class="proof-row {status}" data-proof-status="{status}">'
            f'<strong>{html.escape(str(proof.get("task_id", "proof")))}</strong>'
            f'<span>{html.escape(str(proof.get("summary", "")))}</span>'
            f'<code>{status} / {html.escape(str(proof.get("client", "")))}</code>'
            f'<small>Started {html.escape(str(proof.get("started_at") or "unknown"))} / Finished {html.escape(str(proof.get("finished_at") or "pending"))}</small>'
            f"{recovery_link}"
            "</article>"
        )
    return f'<div class="proof-status-list">{"".join(rows)}</div>'


def render_terminal_status(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<p class="empty">No terminal status recorded.</p>'
    rendered = []
    for row in rows:
        status = html.escape(str(row.get("status", "")))
        kind = html.escape(str(row.get("kind", "")))
        rendered.append(
            f'<article class="terminal-row {status}" data-terminal-kind="{kind}">'
            f"<code>{kind} / {status}</code>"
            f'<span>{html.escape(str(row.get("summary", "")))}</span>'
            f'<small>{html.escape(str(row.get("created_at") or ""))} / {html.escape(str(row.get("client") or ""))} / {html.escape(str(row.get("role") or ""))}</small>'
            "</article>"
        )
    return f'<div class="terminal-list">{"".join(rendered)}</div>'


def render_event_timeline(
    events: list[dict[str, Any]],
    *,
    run_id: str | None = None,
    client: str | None = None,
    status: str | None = None,
    kind: str | None = None,
    role: str | None = None,
) -> str:
    filtered = [
        event
        for event in events
        if (run_id is None or event.get("run_id") == run_id)
        and (client is None or event.get("client") == client or event.get("client_id") == client)
        and (status is None or event.get("status") == status)
        and (kind is None or event.get("kind") == kind)
        and (role is None or event.get("role") == role)
    ]
    if not filtered:
        return '<p class="empty">No events match the current filters.</p>'
    rows = []
    for event in filtered:
        event_kind = html.escape(str(event.get("kind", "")))
        event_status = html.escape(str(event.get("status", "")))
        rows.append(
            f'<article class="timeline-row {event_kind} {event_status}" data-event-kind="{event_kind}" data-event-status="{event_status}">'
            "<div>"
            f'<strong>{html.escape(str(event.get("summary", "")))}</strong>'
            f'<span>{html.escape(str(event.get("created_at") or event.get("updated_at") or ""))}</span>'
            "</div>"
            f"<code>{event_kind} / {event_status}</code>"
            f'<small>{html.escape(str(event.get("run_id") or "no-run"))} / {html.escape(str(event.get("client") or event.get("client_id") or ""))} / {html.escape(str(event.get("role") or ""))}</small>'
            "</article>"
        )
    return f'<div class="timeline-list">{"".join(rows)}</div>'


def render_active_clients(clients: list[dict[str, Any]]) -> str:
    if not clients:
        return '<p class="empty">No clients registered.</p>'
    rows = []
    for client in clients:
        status = html.escape(str(client.get("status", "disconnected")))
        capabilities = ", ".join(str(value) for value in client.get("supported_capabilities", [])) or "no capabilities"
        heartbeat_age = client.get("heartbeat_age_seconds")
        heartbeat_label = "no heartbeat" if heartbeat_age is None else f"{heartbeat_age}s ago"
        rows.append(
            f'<article class="client-row {status}" data-client-status="{status}">'
            "<div>"
            f'<strong>{html.escape(str(client.get("display_name") or client.get("client_id", "")))}</strong>'
            f'<span>{html.escape(str(client.get("client_type", "")))} / {html.escape(str(client.get("connection_mode", "")))}</span>'
            "</div>"
            f"<code>{status}</code>"
            f"<span>{html.escape(str(heartbeat_label))}</span>"
            f"<small>{html.escape(capabilities)}</small>"
            "</article>"
        )
    return f'<div class="client-grid">{"".join(rows)}</div>'


def render_activity_stream(activity: list[dict[str, Any]]) -> str:
    if not activity:
        return '<p class="empty">No activity recorded.</p>'
    rows = []
    for item in activity:
        rows.append(
            '<article class="activity-row">'
            f'<strong>{html.escape(str(item.get("summary", "")))}</strong>'
            f'<span>{html.escape(str(item.get("client", "")))} · {html.escape(str(item.get("actor", "")))} · {html.escape(str(item.get("role", "")))}</span>'
            f'<code>{html.escape(str(item.get("kind", "")))} / {html.escape(str(item.get("status", "")))}</code>'
            "</article>"
        )
    return "\n".join(rows)


def render_work_items(activity: list[dict[str, Any]]) -> str:
    work = [item for item in activity if item.get("status") in {"active", "blocked", "needs_human"}]
    if not work:
        return '<p class="empty">No open work items.</p>'
    items = []
    for item in work:
        task = html.escape(str(item.get("task_id") or item.get("activity_id", "")))
        summary = html.escape(str(item.get("summary", "")))
        status = html.escape(str(item.get("status", "")))
        items.append(f'<li><strong>{task}</strong><span>{summary}</span><code>{status}</code></li>')
    return f'<ul class="work-items">{"".join(items)}</ul>'


def render_kind_view(activity: list[dict[str, Any]], kind: str) -> str:
    filtered = [item for item in activity if item.get("kind") == kind]
    return render_activity_stream(filtered)


def render_plugin_status(plugins: list[dict[str, Any]]) -> str:
    if not plugins:
        return '<p class="empty">No plugin status reported.</p>'
    rows = []
    for plugin in plugins:
        status = html.escape(str(plugin.get("status", "")))
        action = plugin.get("action_required")
        action_markup = f"<small>{html.escape(str(action))}</small>" if action else ""
        rows.append(
            f'<article class="plugin-status-row {status}">'
            f'<strong>{html.escape(str(plugin.get("plugin_id", "")))}</strong>'
            f'<span>{html.escape(str(plugin.get("target_client", "")))} / local {html.escape(str(plugin.get("local_version") or plugin.get("version", "")))}</span>'
            f"<code>{status}</code>"
            f'<small>Publication {html.escape(str(plugin.get("publication_readiness", "needs_review")))}</small>'
            f'<p>{html.escape(str(plugin.get("summary", "")))}</p>'
            f"{action_markup}"
            "</article>"
        )
    return f'<div class="plugin-status-grid">{"".join(rows)}</div>'


def _summary_cards(title: str, counts: dict[str, int]) -> str:
    cells = "".join(
        f'<span><strong>{html.escape(str(value))}</strong>{html.escape(str(name))}</span>'
        for name, value in sorted(counts.items())
    )
    if not cells:
        cells = "<span><strong>0</strong>none</span>"
    return f'<div class="summary-card"><h2>{html.escape(title)}</h2>{cells}</div>'
