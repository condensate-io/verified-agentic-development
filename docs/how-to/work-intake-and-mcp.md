# How-To: Configure Work Intake Connectors & Governed MCP Surface

This operational guide provides complete instructions for integrating external project management and ticketing platforms (GitHub Issues & PRs, GitLab Issues & MRs, Atlassian Jira, and Generic Webhooks) into the VAD Control Plane, and configuring the Model Context Protocol (MCP) surface for autonomous AI coding agents (Claude Code, Cursor, Codex).

---

## 1. Architecture Overview

```
External Systems              VAD Ingestion & Governance Layer            Agent MCP Surface
─────────────────             ────────────────────────────────            ──────────────────
┌─────────────────┐           ┌──────────────────────────────┐            ┌────────────────┐
│  GitHub Issues  │─(Webhook)─▶│ Webhook Ingress & Auth Gate  │            │  Claude Code   │
│  & Pull Requests│◀─(Checks)─│ (HMAC-SHA256, Replay Guard)  │            │  Claude Desktop│
└─────────────────┘           └──────────────┬───────────────┘            │  Cursor Agent  │
┌─────────────────┐                          │                            └───────┬────────┘
│  GitLab Issues  │─(Webhook)────────────────┤                                    │ (JSON-RPC 2.0 /
│  & Merge Reqs   │◀──(REST)─────────────────┤                                    │  stdio or SSE)
└─────────────────┘                          ▼                                    ▼
┌─────────────────┐           ┌──────────────────────────────┐            ┌────────────────┐
│  Atlassian Jira │─(Webhook)─▶│ Universal Work Intake Router │◀──────────▶│ Governed MCP   │
│  Cloud/Server   │◀──(ADF)───│ • Canonical Schema Converter │  Bi-dir    │ Gateway Surface│
└─────────────────┘           │ • Definition of Ready (DoR)  │  Sync      │ • Tool Auth    │
┌─────────────────┐           │ • Risk & Capability Router   │            │ • Lease Claim  │
│ Generic Webhooks│─(Webhook)─▶│ • Token Budget Allocator     │            │ • Evidence Sub │
│ (Linear/Asana)  │           └──────────────┬───────────────┘            │ • 2-Person Gate│
└─────────────────┘                          ▼                            └────────────────┘
                              ┌──────────────────────────────┐
                              │ VAD Control Plane WorkItems  │
                              │ (Queued -> Assigned ->       │
                              │  Running -> Verifying ->     │
                              │  Approved -> Completed)      │
                              └──────────────────────────────┘
```

---

## 2. External Platform Connector Setup

VAD connectors support both real-time webhook ingestion and scheduled polling. Connectors are registered in `vad/connectors/registry.py` or configured via the administration API `/api/v1/connectors`.

### GitHub Connector (`github`)
- **Supported Events:** `issues` (`opened`, `edited`, `labeled`, `assigned`), `pull_request` (`opened`, `synchronize`, `review_requested`).
- **Authentication:** HMAC-SHA256 via `X-Hub-Signature-256` header.
- **Environment Variables:**
  - `VAD_GITHUB_WEBHOOK_SECRET`: Secret configured in your GitHub Repository or Org webhook settings.
  - `VAD_GITHUB_TOKEN`: Personal Access Token or GitHub App installation token for outbound comments and Checks API status.
- **Project Whitelist:** Restricts admission to authorized repositories (e.g. `["enterprise/telephony", "enterprise/core"]`).

### GitLab Connector (`gitlab`)
- **Supported Events:** `Issue Hook`, `Merge Request Hook`.
- **Authentication:** Secret token via `X-Gitlab-Token` header.
- **Environment Variables:**
  - `VAD_GITLAB_WEBHOOK_SECRET`: Secret token configured in GitLab project webhooks.
  - `VAD_GITLAB_TOKEN`: GitLab API token for posting notes and pipeline status badges.

### Atlassian Jira Connector (`jira`)
- **Supported Platforms:** Jira Cloud REST API v3 and Jira Server/Data Center REST API v2.
- **Rich Text Handling:** Automatic sanitization between Atlassian Document Format (ADF) and Markdown to ensure clean rendering.
- **Environment Variables:**
  - `VAD_JIRA_INSTANCE_URL`: e.g. `https://your-domain.atlassian.net`.
  - `VAD_JIRA_API_TOKEN`: Atlassian API Token.
  - `VAD_JIRA_USER_EMAIL`: User account associated with the API token.
  - `VAD_JIRA_WEBHOOK_SECRET`: Shared secret for webhook payload verification.

### Generic Webhook Connector (`generic`)
- **Supported Platforms:** Linear, Asana, Monday, and custom CI/CD pipelines.
- **Payload Schema:** Standardized JSON structure mapping external identifiers, titles, acceptance criteria, and repo URIs to canonical `WorkIntakePayload`.

---

## 3. Ingress Webhook Receiver & Replay Guard

All external webhook traffic enters through the FastAPI endpoint:
```http
POST /api/v1/webhooks/{provider}
```

The Webhook Receiver guarantees:
1. **Cryptographic Validation:** Validates HMAC-SHA256 signatures or tokens before reading the body.
2. **Replay Guard:** Tracks delivery UUIDs (`X-GitHub-Delivery`, `X-Gitlab-Event-UUID`, or `event_id`) with timestamps to prevent replay attacks.
3. **Canonical Normalization:** Converts platform-specific payloads into `WorkIntakePayload`.
4. **Ingress Event Logging:** Emits durable audit records into `ServerStore`.

---

## 4. Definition of Ready (DoR) Automated Gate

Inbound work items are automatically evaluated against the Definition of Ready gate (`vad/policy/intake_dor_policy.py`) before entering the active agent queue:

1. **Safety Guard:** Scans title and description for prohibited prompt injection phrases (e.g. "ignore previous instructions", "system: you are now", shell bombs). Violations immediately transition the ticket to `REJECTED`.
2. **Title Completeness:** Requires a minimum of 5 characters.
3. **Problem Description:** Requires a minimum of 20 characters of substantive context.
4. **Acceptance Criteria:** Inspects explicit criteria lists or markdown checkboxes (`- [ ]`, `* [ ]`) in the description. If missing, marks the ticket `DOR_DEFICIENT`.
5. **Clarification Feedback:** Automatically formats feedback comments posted to the external ticket requesting the author provide missing details.

---

## 5. Governed MCP Surface for AI Agents

VAD exposes a standardized Model Context Protocol (MCP) server over stdio for AI coding assistants.

### Quickstart CLI Command
```bash
# Run stdio server for Claude Code or Cursor
vad mcp work-intake --db .vad/control-plane/vad.sqlite3

# Automatic extension configuration for Claude Code
vad mcp work-intake --install claude

# Automatic extension configuration for Cursor
vad mcp work-intake --install cursor

# Print JSON configuration snippet
vad mcp work-intake --print-config claude
```

### Configuration Snippets

#### Claude Code (`~/.claudecode/config.json`)
```json
{
  "mcpServers": {
    "vad-work-intake": {
      "command": "vad",
      "args": ["mcp", "work-intake", "--db", ".vad/control-plane/vad.sqlite3"]
    }
  }
}
```

#### Cursor (`~/.config/Cursor/User/globalStorage/meta.mcp/config.json`)
```json
{
  "vad-work-intake": {
    "type": "stdio",
    "command": "vad",
    "args": ["mcp", "work-intake", "--db", ".vad/control-plane/vad.sqlite3"],
    "enabled": true
  }
}
```

### Governed Tools Reference

| Tool Name | Risk Tier | Input Parameters | Description |
|-----------|-----------|------------------|-------------|
| `work_intake_discover` | LOW | `role`, `capabilities`, `limit` | Discovers eligible `QUEUED` work items meeting agent capabilities. |
| `work_intake_claim` | MEDIUM | `work_item_id`, `client_id`, `actor`, `lease_duration_seconds` | Claims an exclusive lease on a work item, transitioning it to `ASSIGNED`/`RUNNING`. |
| `work_intake_report_progress` | LOW | `work_item_id`, `lease_id`, `message`, `percent_complete` | Records development milestones and mirrors progress comments to the external ticket. |
| `work_intake_submit_proof` | HIGH | `work_item_id`, `lease_id`, `evidence_digest`, `test_summary` | Submits cryptographic test proof, transitions work item to `VERIFYING`, and triggers release gate. |
| `work_intake_escalate` | HIGH | `work_item_id`, `lease_id`, `blocker_reason` | Escalates an unresolvable blocker to human operators, setting status to `BLOCKED`. |

---

## 6. Two-Person Release Guardian Gate & Attestation

Resolving a remote issue or merging a PR is strictly governed:
- **Attestation Requirement:** Work items must produce a valid 64-character SHA-256 `evidence_digest` generated during test execution in a disposable Docker container.
- **Two-Person Dual Sign-off:** High-risk, production-tier, or security-sensitive tickets require two distinct actors:
  1. Primary Actor (Builder/Verifier Agent): Submits evidence bundle.
  2. Secondary Actor (Human Operator or Release Guardian): Reviews test evidence and grants release sign-off.
- Any attempt by a single actor to provide both sign-offs is automatically rejected.

---

## 7. Bidirectional Sync Worker Operation

The sync worker (`vad/connectors/sync_worker.py`) executes background synchronization between the VAD Control Plane and remote ticketing systems:

```bash
# Programmatic usage in background service:
from vad.connectors.sync_worker import SyncWorker, SyncJob, SyncJobType

worker = SyncWorker()
# Processes pending outbound status changes, comments, attestations, and inbound polls
batch = worker.sync_once()
print(f"Sync complete: {batch.succeeded} succeeded, {batch.held} held by governance, {batch.failed} failed.")
```

- **Outbound Resolution Protection:** If a work item reaches `COMPLETED` but lacks 2-person sign-off, the sync worker marks the job `HELD_BY_GOVERNANCE` and posts an explanatory hold notice on the ticket.
- **Automatic Retry Backoff:** Transient network errors are automatically retried up to `max_retries` (default 3) before marking the job `FAILED`.
