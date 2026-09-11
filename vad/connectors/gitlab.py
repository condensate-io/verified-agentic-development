from __future__ import annotations

import hmac
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


class GitLabConnector(BaseWorkConnector):
    """GitLab connector supporting Issues, Merge Requests, Notes, and secret-token webhooks."""

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self.posted_comments: List[Dict[str, Any]] = []
        self.status_transitions: List[Dict[str, Any]] = []
        self.attestations: List[Dict[str, Any]] = []

    def validate_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        normalized = self.normalize_headers(headers)
        token = normalized.get("x-gitlab-token")
        if not self.config.secret_token:
            return False
        if not token:
            return False
        return hmac.compare_digest(self.config.secret_token, token)

    def parse_webhook_event(
        self, headers: dict[str, str], payload: dict[str, Any]
    ) -> Optional[WorkIntakePayload]:
        normalized = self.normalize_headers(headers)
        event_header = normalized.get("x-gitlab-event")
        object_kind = payload.get("object_kind")

        # Handle Issues
        if object_kind == "issue" or event_header == "Issue Hook":
            attrs = payload.get("object_attributes", {})
            action = attrs.get("action")
            if action not in ("open", "reopen", "update"):
                return None

            project = payload.get("project", {})
            project_path = project.get("path_with_namespace") or "unknown/project"

            if not self.is_project_allowed(project_path):
                return None

            raw_labels = [l.get("title", "") for l in payload.get("labels", [])]
            if not self.matches_labels(raw_labels):
                return None

            desc = attrs.get("description") or ""
            ac_list = self._extract_acceptance_criteria(desc)

            ref = ExternalWorkRef(
                platform=ConnectorPlatform.GITLAB,
                instance_url=self.config.instance_url,
                remote_id=str(attrs.get("id")),
                remote_key=f"#{attrs.get('iid')}",
                project_id=project_path,
                web_url=attrs.get("url"),
                remote_status=attrs.get("state", "opened"),
                author=(payload.get("user") or {}).get("username"),
                metadata={"action": action, "project": project_path, "labels": raw_labels},
            )

            return WorkIntakePayload(
                ref=ref,
                title=attrs.get("title", ""),
                description=desc,
                acceptance_criteria=ac_list,
                labels=raw_labels,
                target_repo=project_path,
                is_pull_request=False,
                raw_event_type="issue",
            )

        # Handle Merge Requests
        elif object_kind == "merge_request" or event_header == "Merge Request Hook":
            attrs = payload.get("object_attributes", {})
            action = attrs.get("action")
            if action not in ("open", "reopen", "update"):
                return None

            project = payload.get("project", {})
            project_path = project.get("path_with_namespace") or "unknown/project"

            if not self.is_project_allowed(project_path):
                return None

            raw_labels = [l.get("title", "") for l in payload.get("labels", [])]
            if not self.matches_labels(raw_labels):
                return None

            source_branch = attrs.get("source_branch")
            last_commit = attrs.get("last_commit", {})
            commit_sha = last_commit.get("id")

            ref = ExternalWorkRef(
                platform=ConnectorPlatform.GITLAB,
                instance_url=self.config.instance_url,
                remote_id=str(attrs.get("id")),
                remote_key=f"!{attrs.get('iid')}",
                project_id=project_path,
                web_url=attrs.get("url"),
                remote_status=attrs.get("state", "opened"),
                author=(payload.get("user") or {}).get("username"),
                metadata={"action": action, "commit_sha": commit_sha, "branch": source_branch},
            )

            return WorkIntakePayload(
                ref=ref,
                title=attrs.get("title", ""),
                description=attrs.get("description") or "",
                acceptance_criteria=[],
                labels=raw_labels,
                target_repo=project_path,
                branch=source_branch,
                is_pull_request=True,
                raw_event_type="merge_request",
            )

        return None

    def poll_work_items(self, query: str | None = None) -> list[WorkIntakePayload]:
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
        note = (
            f"### 🛡️ GitLab VAD Verification Attestation\n\n"
            f"- **Digest:** `{attestation.evidence_digest}`\n"
            f"- **Status:** {attestation.status.upper()}\n"
            f"- **Verified:** {'✅ Passed' if attestation.verified else '❌ Failed'}\n"
            f"- **Summary:** {attestation.test_summary}\n"
        )
        if attestation.commit_sha:
            note += f"- **Commit SHA:** `{attestation.commit_sha}`\n"
        return self.post_comment(attestation.ref, note)

    @staticmethod
    def _extract_acceptance_criteria(body: str) -> List[str]:
        ac_lines = []
        for line in body.splitlines():
            clean = line.strip()
            if re.match(r"^[-*]\s*\[[ xX]\]\s+", clean):
                item = re.sub(r"^[-*]\s*\[[ xX]\]\s+", "", clean).strip()
                if item:
                    ac_lines.append(item)
        return ac_lines


# Register in global registry
get_connector_registry().register_connector_class(
    ConnectorPlatform.GITLAB, GitLabConnector
)
