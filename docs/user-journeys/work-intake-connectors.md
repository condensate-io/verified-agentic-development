# User Journey: Work Intake Connectors & Governed AI Execution

This user journey describes the lifecycle of an engineering requirement as it originates in an external project management system (such as Jira or GitHub Issues), transitions through VAD's automated intake governance, is safely claimed and executed by an autonomous AI agent (Claude Code or Cursor) via MCP, and undergoes two-person release verification before remote ticket resolution.

---

## 1. Journey Overview & Participants

| Role | Persona | System Used | Responsibilities |
|------|---------|-------------|------------------|
| **Product / Tech Lead** | Sarah | GitHub / Jira | Defines user stories, acceptance criteria, and priority. |
| **Ingress Governance** | VAD Control Plane | Webhook Receiver & DoR Gate | Validates HMAC signature, replay protection, and checks Definition of Ready. |
| **Autonomous AI Agent** | Claude Code / Cursor | Governed MCP Surface | Discovers queued work, acquires lease, implements solution, and generates proof. |
| **Release Guardian** | Alex (Lead SRE) | Operator TUI / CLI | Reviews verifier evidence digest and signs off on release promotion. |
| **Sync Worker** | VAD Sync Worker | Bidirectional Connectors | Posts progress comments, attaches attestations, and closes remote issues. |

```
Sarah (Tech Lead)             VAD Control Plane              Claude Code Agent             Alex (Release Guardian)
       │                              │                              │                               │
       │ 1. Creates Jira/GitHub Issue │                              │                               │
       ├─────────────────────────────▶│                              │                               │
       │                              │ 2. Webhook Ingress & DoR Gate│                               │
       │                              │───(Evaluates Criteria)───┐   │                               │
       │                              │                          │   │                               │
       │                              │◀─────────────────────────┘   │                               │
       │                              │                              │                               │
       │                              │ 3. Discover Queued Work      │                               │
       │                              │◀─────────────────────────────┤                               │
       │                              │                              │                               │
       │                              │ 4. Claim Task Lease (RUNNING)│                               │
       │                              │◀─────────────────────────────┤                               │
       │                              │                              │                               │
       │ 5. Progress Comment Mirrored │                              │ 5. Reports Progress (50%)     │
       │◀─────────────────────────────┼──────────────────────────────┤                               │
       │                              │                              │                               │
       │                              │                              │ 6. Executes Tests in Docker   │
       │                              │                              │───(Generates Evidence Digest)─│
       │                              │                              │                               │
       │                              │ 7. Submits Proof (VERIFYING) │                               │
       │                              │◀─────────────────────────────┤                               │
       │                              │                                                              │
       │                              │ 8. Two-Person Release Gate Evaluation                        │
       │                              │◀─────────────────────────────────────────────────────────────┤ (Reviews Evidence)
       │                              │                                                              │ 9. Dual Sign-off
       │                              │                                                              │
       │ 10. Remote Issue Closed with │                              │                               │
       │     Attestation & DSSE digest│                              │                               │
       │◀─────────────────────────────┤                              │                               │
```

---

## 2. Stage-by-Stage Journey

### Stage 1: Ticket Creation in External Platform
Sarah opens an issue in GitHub:
- **Title:** `Fix buffer overrun in SIP header parser`
- **Description:**
  ```markdown
  The SIP parser crashes when receiving a Via header exceeding 4096 bytes.
  
  Acceptance criteria:
  - [ ] Add explicit bounds checking in `sip_parser.c`
  - [ ] Reject headers over 4KB with HTTP 400 Bad Request
  - [ ] Pass full regression suite with zero memory leaks
  ```
- **Labels:** `vad:governed`, `security`, `high-priority`

### Stage 2: Webhook Ingress & DoR Automated Gate
1. GitHub fires a webhook to VAD: `POST /api/v1/webhooks/github`.
2. VAD validates `X-Hub-Signature-256` against `VAD_GITHUB_WEBHOOK_SECRET`.
3. Replay guard checks the delivery ID `X-GitHub-Delivery` to ensure the event has not been processed.
4. The Definition of Ready (DoR) gate checks:
   - Title length >= 5 characters (PASS).
   - Description >= 20 characters (PASS).
   - Acceptance criteria checkboxes present (PASS).
   - Content safety guard against prompt injection (PASS).
5. VAD provisions a durable `WorkItem` with status `QUEUED` and priority `100`.

### Stage 3: Autonomous Agent Discovers & Claims Work
Claude Code connects via stdio MCP:
```bash
vad mcp work-intake
```
1. **Discovery:** The agent calls `work_intake_discover(role="developer")`. VAD returns the queued SIP parser work item.
2. **Claim:** The agent calls `work_intake_claim(work_item_id="...", client_id="claude-code")`. VAD creates an exclusive 10-minute lease and moves the work item to `ASSIGNED` / `RUNNING`.
3. **Progress Reporting:** As Claude Code implements the C bounds check, it calls `work_intake_report_progress(message="Implemented bounds check; running AFL fuzzing", percent_complete=60)`. VAD automatically posts a comment to the GitHub issue so Sarah can see live progress.

### Stage 4: Verification in Disposable Docker & Proof Submission
1. Claude Code executes tests inside the disposable Docker runner:
   ```bash
   wsl docker run --rm -v $(pwd):/workspace vad-runner:3.11 pytest tests/
   ```
2. Tests pass, producing a 64-character SHA-256 evidence bundle digest `f1a4...`.
3. Agent calls MCP tool `work_intake_submit_proof(evidence_digest="f1a4...", test_summary="15 unit tests and fuzzing passed")`.
4. Work item status transitions to `VERIFYING`.

### Stage 5: Release Guardian 2-Person Dual Sign-off
Because the task is high-risk and security-sensitive, VAD's `IntakeReleaseGate` activates:
- **First Sign-off:** Claude Code (verifier agent) provides the initial attestation.
- **Second Sign-off Required:** Alex (Release Guardian / Lead SRE) inspects the evidence digest via the VAD Operator TUI or CLI:
  ```bash
  vad evidence inspect .vad/evidence/f1a4...
  vad operator-intents approve --work-item-id wi-100 --actor alex --role release_guardian
  ```
- Any attempt by the agent to sign off on its own work is rejected with `Dual sign-off requires at least two distinct actors`.

### Stage 6: Outbound Ticket Resolution & Attestation
Once both distinct sign-offs are verified:
1. `IntakeReleaseGate` returns `decision=ALLOWED`, `can_close_remote_ticket=True`.
2. The `SyncWorker` executes outbound jobs:
   - `attach_attestation`: Attaches the evidence digest and commit SHA to GitHub Checks API.
   - `sync_status`: Closes the GitHub issue with an attestation summary:
     ```
     [VAD Verified Release] Issue resolved by Claude Code agent.
     Evidence Digest: f1a4d982b6...
     Release Guardian Sign-off: Alex (Lead SRE)
     Status: COMPLETED (Dual Sign-off Verified)
     ```
3. Sarah and the engineering team have full traceability from the original requirement to the verified code and release sign-off.
