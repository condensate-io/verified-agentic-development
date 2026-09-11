from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any, Dict, List, Optional

from vad.connectors.base import BaseWorkConnector
from vad.connectors.models import (
    AttestationSyncPayload,
    ConnectorConfig,
    ConnectorPlatform,
    ExternalWorkRef,
    OutboundTransitionRequest,
    WorkIntakePayload,
)
from vad.connectors.registry import get_connector_registry


class GitHubConnector(BaseWorkConnector):
    """GitHub connector supporting Issues, Pull Requests, Checks API, and HMAC webhooks."""

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self.posted_comments: List[Dict[str, Any]] = []
        self.status_transitions: List[Dict[str, Any]] = []
        self.attestations: List[Dict[str, Any]] = []

    def validate_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        normalized = self.normalize_headers(headers)
        signature = normalized.get("x-hub-signature-256")
        if not self.config.webhook_secret:
            # If no secret configured, reject unless explicitly unauthenticated
            return False
        if not signature or not signature.startswith("sha256="):
            return False

        expected_sig = "sha256=" + hmac.new(
            self.config.webhook_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected_sig, signature)

    def parse_webhook_event(
        self, headers: dict[str, str], payload: dict[str, Any]
    ) -> Optional[WorkIntakePayload]:
        normalized = self.normalize_headers(headers)
        event_type = normalized.get("x-github-event") or payload.get("action_type")

        # Handle Issues
        if event_type == "issues":
            action = payload.get("action")
            if action not in ("opened", "labeled", "assigned", "reopened"):
                return None

            issue = payload.get("issue", {})
            repo = payload.get("repository", {})
            repo_name = repo.get("full_name") or "unknown/repo"

            if not self.is_project_allowed(repo_name):
                return None

            raw_labels = [l.get("name", "") for l in issue.get("labels", [])]
            if not self.matches_labels(raw_labels):
                return None

            body_text = issue.get("body") or ""
            ac_list = self._extract_acceptance_criteria(body_text)

            ref = ExternalWorkRef(
                platform=ConnectorPlatform.GITHUB,
                instance_url=self.config.instance_url,
                remote_id=str(issue.get("id")),
                remote_key=f"#{issue.get('number')}",
                project_id=repo_name,
                web_url=issue.get("html_url"),
                remote_status=issue.get("state", "open"),
                author=(issue.get("user") or {}).get("login"),
                metadata={"action": action, "repo": repo_name, "raw_labels": raw_labels},
            )

            return WorkIntakePayload(
                ref=ref,
                title=issue.get("title", ""),
                description=body_text,
                acceptance_criteria=ac_list,
                labels=raw_labels,
                target_repo=repo_name,
                is_pull_request=False,
                raw_event_type="issues",
            )

        # Handle Pull Requests
        elif event_type == "pull_request":
            action = payload.get("action")
            if action not in ("opened", "synchronize", "review_requested", "reopened"):
                return None

            pr = payload.get("pull_request", {})
            repo = payload.get("repository", {})
            repo_name = repo.get("full_name") or "unknown/repo"

            if not self.is_project_allowed(repo_name):
                return None

            raw_labels = [l.get("name", "") for l in pr.get("labels", [])]
            if not self.matches_labels(raw_labels):
                return None

            head_data = pr.get("head", {})
            branch_name = head_data.get("ref")
            commit_sha = head_data.get("sha")

            ref = ExternalWorkRef(
                platform=ConnectorPlatform.GITHUB,
                instance_url=self.config.instance_url,
                remote_id=str(pr.get("id")),
                remote_key=f"#{pr.get('number')}",
                project_id=repo_name,
                web_url=pr.get("html_url"),
                remote_status=pr.get("state", "open"),
                author=(pr.get("user") or {}).get("login"),
                metadata={"action": action, "commit_sha": commit_sha, "branch": branch_name},
            )

            return WorkIntakePayload(
                ref=ref,
                title=pr.get("title", ""),
                description=pr.get("body") or "",
                acceptance_criteria=[],
                labels=raw_labels,
                target_repo=repo_name,
                branch=branch_name,
                is_pull_request=True,
                raw_event_type="pull_request",
            )

        return None

    def poll_work_items(self, query: str | None = None) -> list[WorkIntakePayload]:
        # Polling placeholder or simulated response
        return []

    def sync_status(self, transition: OutboundTransitionRequest) -> bool:
        self.status_transitions.append(transition.model_dump())
        if transition.transition_note:
            self.post_comment(transition.ref, transition.transition_note)
        return True

    def post_comment(
        self, ref: ExternalWorkRef, comment: str, is_internal: bool = False
    ) -> bool:
        self.posted_comments.append({
            "ref": ref.model_dump(),
            "comment": comment,
            "is_internal": is_internal,
        })
        return True

    def attach_attestation(self, attestation: AttestationSyncPayload) -> bool:
        self.attestations.append(attestation.model_dump())
        comment = (
            f"### 🛡️ VAD Verification Attestation\n\n"
            f"- **Digest:** `{attestation.evidence_digest}`\n"
            f"- **Status:** {attestation.status.upper()}\n"
            f"- **Verified:** {'✅ Yes' if attestation.verified else '❌ No'}\n"
            f"- **Summary:** {attestation.test_summary}\n"
        )
        if attestation.commit_sha:
            comment += f"- **Commit:** `{attestation.commit_sha}`\n"
        return self.post_comment(attestation.ref, comment)

    @staticmethod
    def _extract_acceptance_criteria(body: str) -> List[str]:
        ac_lines = []
        for line in body.splitlines():
            clean = line.strip()
            # Match markdown checkboxes: - [ ] criterion or - [x] criterion
            if re.match(r"^[-*]\s*\[[ xX]\]\s+", clean):
                item = re.sub(r"^[-*]\s*\[[ xX]\]\s+", "", clean).strip()
                if item:
                    ac_lines.append(item)
        return ac_lines


# Register in global registry
get_connector_registry().register_connector_class(
    ConnectorPlatform.GITHUB, GitHubConnector
)
