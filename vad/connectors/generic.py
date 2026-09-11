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


class GenericWebhookConnector(BaseWorkConnector):
    """Generic & configurable connector supporting Linear, Asana, Monday, Azure DevOps, and custom webhooks."""

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self.posted_comments: List[Dict[str, Any]] = []
        self.status_transitions: List[Dict[str, Any]] = []
        self.attestations: List[Dict[str, Any]] = []

    def validate_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        normalized = self.normalize_headers(headers)
        secret = self.config.webhook_secret or self.config.secret_token
        if not secret:
            # If no secret required, accept
            return True

        header_token = (
            normalized.get("x-webhook-token")
            or normalized.get("x-linear-signature")
            or normalized.get("x-hook-secret")
            or normalized.get("authorization", "").replace("Bearer ", "").strip()
        )
        if not header_token:
            return False
        return hmac.compare_digest(secret, header_token)

    def parse_webhook_event(
        self, headers: dict[str, str], payload: dict[str, Any]
    ) -> Optional[WorkIntakePayload]:
        # Detect Linear payload
        if payload.get("type") == "Issue" and "data" in payload:
            return self._parse_linear(payload)

        # Detect Generic JSON payload
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        remote_id = str(data.get("id") or data.get("ticket_id") or data.get("issue_id") or "")
        title = data.get("title") or data.get("summary") or data.get("name")
        if not remote_id or not title:
            return None

        project = str(data.get("project") or data.get("project_id") or "default")
        if not self.is_project_allowed(project):
            return None

        raw_labels = data.get("labels") or data.get("tags") or []
        if isinstance(raw_labels, str):
            raw_labels = [s.strip() for s in raw_labels.split(",") if s.strip()]
        if not self.matches_labels(raw_labels):
            return None

        desc = str(data.get("description") or data.get("body") or "")
        ac_list = self._extract_acceptance_criteria(desc)

        ref = ExternalWorkRef(
            platform=self.config.platform,
            instance_url=self.config.instance_url,
            remote_id=remote_id,
            remote_key=str(data.get("key") or data.get("identifier") or f"GEN-{remote_id}"),
            project_id=project,
            web_url=data.get("url") or f"{self.config.instance_url.rstrip('/')}/issues/{remote_id}",
            remote_status=str(data.get("status") or "open"),
            author=data.get("author") or data.get("creator"),
            metadata={"raw": payload},
        )

        return WorkIntakePayload(
            ref=ref,
            title=title,
            description=desc,
            acceptance_criteria=ac_list,
            priority=int(data.get("priority") or 100),
            role=str(data.get("role") or self.config.default_role),
            labels=raw_labels,
            target_repo=data.get("target_repo") or data.get("repo"),
            is_pull_request=bool(data.get("is_pull_request", False)),
            raw_event_type="generic:work_item",
        )

    def _parse_linear(self, payload: dict[str, Any]) -> Optional[WorkIntakePayload]:
        action = payload.get("action")
        if action not in ("create", "update"):
            return None
        data = payload.get("data", {})
        identifier = data.get("identifier") or f"LIN-{data.get('id')}"
        team = (data.get("team") or {}).get("key") or "DEFAULT"

        if not self.is_project_allowed(team):
            return None

        labels = [l.get("name", "") for l in data.get("labels", []) if isinstance(l, dict)]
        if not self.matches_labels(labels):
            return None

        desc = data.get("description") or ""
        ac_list = self._extract_acceptance_criteria(desc)

        ref = ExternalWorkRef(
            platform=ConnectorPlatform.LINEAR,
            instance_url=self.config.instance_url,
            remote_id=str(data.get("id")),
            remote_key=identifier,
            project_id=team,
            web_url=data.get("url"),
            remote_status=((data.get("state") or {}).get("name") or "Open"),
        )

        return WorkIntakePayload(
            ref=ref,
            title=data.get("title", ""),
            description=desc,
            acceptance_criteria=ac_list,
            priority=int(data.get("priority") or 100),
            labels=labels,
            target_repo=f"linear/{team.lower()}",
            raw_event_type="linear:issue",
        )

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
            f"### 🛡️ VAD Verification Attestation\n\n"
            f"- **Digest:** `{attestation.evidence_digest}`\n"
            f"- **Status:** {attestation.status.upper()}\n"
            f"- **Summary:** {attestation.test_summary}\n"
        )
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


# Register in global registry for multiple connector platforms
for plat in (
    ConnectorPlatform.GENERIC,
    ConnectorPlatform.LINEAR,
    ConnectorPlatform.ASANA,
    ConnectorPlatform.MONDAY,
    ConnectorPlatform.AZURE_DEVOPS,
):
    get_connector_registry().register_connector_class(plat, GenericWebhookConnector)
