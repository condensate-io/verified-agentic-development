"""Managed Settings Generator: Produces enterprise deployment configs (Apple MDM .plist, Windows GPO .admx, and JSON bundles)."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class EnterprisePolicyConfig(BaseModel):
    organization: str = "Enterprise Engineering"
    bundle_id: str = "com.vad.governance"
    enforce_vad_hooks: bool = True
    audit_endpoint: str = "https://vad-audit.internal/api/events"
    allowed_tools: List[str] = Field(default_factory=lambda: ["read", "pytest", "git_status"])
    blocked_tools: List[str] = Field(default_factory=lambda: ["rm -rf", "git push --force"])
    max_budget_per_session_usd: float = 5.0
    require_human_push_confirmation: bool = True


class ManagedSettingsGenerator:
    """Generates cross-platform enterprise configuration profiles enforcing VAD governance."""

    def __init__(self, config: Optional[EnterprisePolicyConfig] = None):
        self.config = config or EnterprisePolicyConfig()

    def generate_json_bundle(self) -> str:
        data = {
            "version": "1.0",
            "organization": self.config.organization,
            "governance": {
                "enforce_vad_hooks": self.config.enforce_vad_hooks,
                "audit_endpoint": self.config.audit_endpoint,
                "allowed_tools": self.config.allowed_tools,
                "blocked_tools": self.config.blocked_tools,
                "max_budget_per_session_usd": self.config.max_budget_per_session_usd,
                "require_human_push_confirmation": self.config.require_human_push_confirmation,
            },
        }
        return json.dumps(data, indent=2)

    def generate_apple_mdm_plist(self) -> str:
        """Generates Apple MDM / macOS Configuration Profile XML plist."""
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
            '<plist version="1.0">',
            '<dict>',
            '    <key>PayloadType</key>',
            f'    <string>{self.config.bundle_id}</string>',
            '    <key>PayloadOrganization</key>',
            f'    <string>{self.config.organization}</string>',
            '    <key>EnforceVADHooks</key>',
            f'    <{"true" if self.config.enforce_vad_hooks else "false"}/>',
            '    <key>AuditEndpoint</key>',
            f'    <string>{self.config.audit_endpoint}</string>',
            '    <key>RequireHumanPushConfirmation</key>',
            f'    <{"true" if self.config.require_human_push_confirmation else "false"}/>',
            '    <key>MaxBudgetUSD</key>',
            f'    <real>{self.config.max_budget_per_session_usd}</real>',
            '</dict>',
            '</plist>',
        ]
        return "\n".join(lines)

    def generate_windows_admx(self) -> str:
        """Generates Windows Group Policy ADMX XML policy template."""
        lines = [
            '<?xml version="1.0" encoding="utf-8"?>',
            '<policyDefinitions xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" revision="1.0" schemaVersion="1.0" xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">',
            '  <policyNamespaces>',
            f'    <target prefix="vad" namespace="{self.config.bundle_id}" />',
            '  </policyNamespaces>',
            '  <resources minRequiredRevision="1.0" />',
            '  <categories>',
            '    <category name="VADGovernance" displayName="Verified Agentic Development" />',
            '  </categories>',
            '  <policies>',
            '    <policy name="EnforceVADHooks" class="Machine" displayName="Enforce VAD Lifecycle Hooks" explainText="Enforces mandatory VAD PreToolUse and Stop hooks." key="Software\\Policies\\VAD" valueName="EnforceHooks">',
            '      <parentCategory ref="VADGovernance" />',
            '      <supportedOn ref="windows:SUPPORTED_Windows10" />',
            '      <enabledValue><decimal value="1" /></enabledValue>',
            '      <disabledValue><decimal value="0" /></disabledValue>',
            '    </policy>',
            '  </policies>',
            '</policyDefinitions>',
        ]
        return "\n".join(lines)

    def export_all(self, output_dir: Path) -> Dict[str, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "vad_settings.json"
        plist_path = output_dir / "vad_policy.plist"
        admx_path = output_dir / "vad_policy.admx"

        json_path.write_text(self.generate_json_bundle(), encoding="utf-8")
        plist_path.write_text(self.generate_apple_mdm_plist(), encoding="utf-8")
        admx_path.write_text(self.generate_windows_admx(), encoding="utf-8")

        return {
            "json": json_path,
            "plist": plist_path,
            "admx": admx_path,
        }
