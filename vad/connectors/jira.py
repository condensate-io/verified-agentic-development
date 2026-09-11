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


class JiraConnector(BaseWorkConnector):
    """Jira Cloud & Data Center connector supporting ADF, JQL mapping, and workflow transitions."""

    PRIORITY_MAP = {
        "highest": 250,
        "high": 180,
        "medium": 100,
        "low": 50,
        "lowest": 20,
    }

    STATUS_TRANSITION_MAP = {
        "running": "In Progress",
        "blocked": "Blocked",
        "verifying": "Under Review",
        "approved": "Ready for Deploy",
        "completed": "Done",
        "closed": "Closed",
    }

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self.posted_comments: List[Dict[str, Any]] = []
        self.status_transitions: List[Dict[str, Any]] = []
        self.attestations: List[Dict[str, Any]] = []

    def validate_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        normalized = self.normalize_headers(headers)
        secret = self.config.webhook_secret or self.config.secret_token
        if not secret:
            return False

        header_token = (
            normalized.get("x-atlassian-webhook-secret")
            or normalized.get("x-jira-secret")
            or normalized.get("authorization", "").replace("Bearer ", "").strip()
        )
        if not header_token:
            return False
        return hmac.compare_digest(secret, header_token)

    def parse_webhook_event(
        self, headers: dict[str, str], payload: dict[str, Any]
    ) -> Optional[WorkIntakePayload]:
        event_name = payload.get("webhookEvent") or ""
        if not (event_name.startswith("jira:issue_created") or event_name.startswith("jira:issue_updated")):
            # Also accept standard generic events
            if "issue" not in payload:
                return None

        issue = payload.get("issue", {})
        fields = issue.get("fields", {})
        project = fields.get("project", {})
        project_key = project.get("key") or "UNKNOWN"

        if not self.is_project_allowed(project_key):
            return None

        raw_labels = fields.get("labels", [])
        if not self.matches_labels(raw_labels):
            return None

        # Convert description if ADF or plain string
        raw_desc = fields.get("description")
        desc_text = self.adf_to_markdown(raw_desc) if isinstance(raw_desc, dict) else (raw_desc or "")
        ac_list = self._extract_acceptance_criteria(desc_text)

        priority_name = ((fields.get("priority") or {}).get("name") or "medium").lower()
        priority_val = self.PRIORITY_MAP.get(priority_name, 100)

        # Inspect custom fields if present
        target_repo = fields.get("customfield_repo") or f"jira/{project_key.lower()}"
        mees = int(fields.get("customfield_mees") or 80)

        ref = ExternalWorkRef(
            platform=ConnectorPlatform.JIRA,
            instance_url=self.config.instance_url,
            remote_id=str(issue.get("id")),
            remote_key=issue.get("key", "UNKNOWN-0"),
            project_id=project_key,
            web_url=f"{self.config.instance_url.rstrip('/')}/browse/{issue.get('key')}",
            remote_status=((fields.get("status") or {}).get("name") or "Open"),
            author=((fields.get("creator") or fields.get("reporter") or {}).get("displayName")),
            metadata={"priority": priority_name, "issue_type": (fields.get("issuetype") or {}).get("name")},
        )

        return WorkIntakePayload(
            ref=ref,
            title=fields.get("summary", ""),
            description=desc_text,
            acceptance_criteria=ac_list,
            priority=priority_val,
            labels=raw_labels,
            target_repo=target_repo,
            mees_estimate=mees,
            is_pull_request=False,
            raw_event_type=event_name or "jira:issue",
        )

    def poll_work_items(self, query: str | None = None) -> list[WorkIntakePayload]:
        return []

    def sync_status(self, transition: OutboundTransitionRequest) -> bool:
        remote_target = self.STATUS_TRANSITION_MAP.get(
            transition.target_status.lower(), transition.target_status
        )
        record = transition.model_dump()
        record["remote_target_status"] = remote_target
        self.status_transitions.append(record)

        if transition.transition_note:
            self.post_comment(transition.ref, transition.transition_note)
        return True

    def post_comment(
        self, ref: ExternalWorkRef, comment: str, is_internal: bool = False
    ) -> bool:
        adf_body = self.markdown_to_adf(comment)
        self.posted_comments.append({
            "ref": ref.model_dump(),
            "comment": comment,
            "adf": adf_body,
            "is_internal": is_internal,
        })
        return True

    def attach_attestation(self, attestation: AttestationSyncPayload) -> bool:
        self.attestations.append(attestation.model_dump())
        comment = (
            f"h3. 🛡️ VAD Verification Attestation\n\n"
            f"* *Evidence Digest:* {attestation.evidence_digest}\n"
            f"* *Verification Status:* {'✅ PASSED' if attestation.verified else '❌ FAILED'}\n"
            f"* *Test Summary:* {attestation.test_summary}\n"
        )
        if attestation.commit_sha:
            comment += f"* *Commit SHA:* {attestation.commit_sha}\n"
        return self.post_comment(attestation.ref, comment)

    @classmethod
    def markdown_to_adf(cls, text: str) -> Dict[str, Any]:
        """Converts text/markdown to canonical Atlassian Document Format (ADF)."""
        paragraphs = []
        for line in text.splitlines():
            clean = line.strip()
            if clean:
                paragraphs.append({
                    "type": "paragraph",
                    "content": [{"type": "text", "text": clean}],
                })
        if not paragraphs:
            paragraphs.append({"type": "paragraph", "content": []})
        return {
            "version": 1,
            "type": "doc",
            "content": paragraphs,
        }

    @classmethod
    def adf_to_markdown(cls, adf: Dict[str, Any]) -> str:
        """Recursively extracts plain text from ADF structure."""
        text_chunks: List[str] = []

        def extract_nodes(node: Any):
            if isinstance(node, dict):
                if node.get("type") == "text" and "text" in node:
                    text_chunks.append(node["text"])
                for v in node.values():
                    extract_nodes(v)
            elif isinstance(node, list):
                for item in node:
                    extract_nodes(item)

        extract_nodes(adf)
        return " ".join(text_chunks).strip()

    @staticmethod
    def _extract_acceptance_criteria(body: str) -> List[str]:
        ac_lines = []
        for line in body.splitlines():
            clean = line.strip()
            # Match checkboxes - [ ] or * [ ] or numbered/bulleted AC
            if re.match(r"^[-*]\s*\[[ xX]\]\s+", clean):
                item = re.sub(r"^[-*]\s*\[[ xX]\]\s+", "", clean).strip()
                if item:
                    ac_lines.append(item)
            elif clean.lower().startswith("ac:") or clean.lower().startswith("acceptance:"):
                item = re.sub(r"^(ac|acceptance):\s*", "", clean, flags=re.IGNORECASE).strip()
                if item:
                    ac_lines.append(item)
        return ac_lines


# Register in global registry
get_connector_registry().register_connector_class(
    ConnectorPlatform.JIRA, JiraConnector
)
