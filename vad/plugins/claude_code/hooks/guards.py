"""PreToolUse Python Guards.

Ports the standalone pre_tool_use bash verification gates into native Python:
1. CredentialGuard: blocks Write/Edit content containing sensitive secrets.
2. DestructiveBashGuard: evaluates destructive-bash-rules.json with deny and ask tiers.
3. ReadPrivacyGuard: blocks reads of .env*, *.pem, and id_rsa*.
4. AgentIntegrityGuard: hash-checks custom subagent types against the integrity registry.
5. EIPReadinessGuard: blocks EIP dispatches unless status: ready and no open questions.

Every guard supports a documented *_GUARD=off escape hatch.
"""

from enum import Enum
import fnmatch
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from vad.integrity.registry import IntegrityRegistry


class GuardDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class GuardResult(BaseModel):
    allowed: bool
    decision: GuardDecision
    reason: str
    guard_name: str
    bypassed: bool = False


# ============================================================================
# 1. Credential Guard
# ============================================================================

class CredentialGuard:
    """Blocks Write/Edit tool invocations if content contains secret patterns."""

    SECRET_PATTERNS = [
        (re.compile(r"-----BEGIN\s+(?:[A-Z0-9_-]+\s+)*PRIVATE KEY", re.IGNORECASE), "Private cryptographic key block"),
        (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key ID"),
        (re.compile(r"sk-[a-zA-Z0-9_-]{20,}"), "OpenAI API Secret Key"),
        (re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36}"), "GitHub Personal Access Token"),
        (re.compile(r"xox[baprs]-[0-9a-zA-Z]{10,}"), "Slack API Token"),
        (re.compile(r"(?:api[_-]?key|secret[_-]?key|auth[_-]?token|password)\s*[:=]\s*[\"'][A-Za-z0-9+/=_.!-]{12,}[\"']", re.IGNORECASE), "High-entropy credential assignment"),
    ]

    WRITE_TOOLS = {
        "write", "edit", "write_file", "edit_file", "str_replace_editor",
        "create_file", "write_to_file", "replace_file_content"
    }

    def evaluate(self, tool_name: str, tool_input: Dict[str, Any]) -> GuardResult:
        if os.getenv("CREDENTIAL_GUARD", "on").lower() == "off":
            return GuardResult(
                allowed=True,
                decision=GuardDecision.ALLOW,
                reason="Escape hatch active (CREDENTIAL_GUARD=off)",
                guard_name="CredentialGuard",
                bypassed=True,
            )

        norm_name = tool_name.lower()
        if not any(w in norm_name for w in self.WRITE_TOOLS):
            return GuardResult(allowed=True, decision=GuardDecision.ALLOW, reason="Non-write tool", guard_name="CredentialGuard")

        content = ""
        for key in ("content", "new_string", "file_text", "text", "body", "replacementcontent", "codecontent"):
            val = tool_input.get(key)
            if isinstance(val, str):
                content += "\n" + val

        if not content:
            return GuardResult(allowed=True, decision=GuardDecision.ALLOW, reason="No write content", guard_name="CredentialGuard")

        for regex, desc in self.SECRET_PATTERNS:
            if regex.search(content):
                return GuardResult(
                    allowed=False,
                    decision=GuardDecision.DENY,
                    reason=f"[CredentialGuard Block] Potential secret detected in Write/Edit payload: {desc}. Set CREDENTIAL_GUARD=off to bypass if intentional fixture.",
                    guard_name="CredentialGuard",
                )

        return GuardResult(allowed=True, decision=GuardDecision.ALLOW, reason="No credentials detected", guard_name="CredentialGuard")


# ============================================================================
# 2. Destructive Bash Guard
# ============================================================================

class DestructiveBashGuard:
    """Evaluates shell commands against data-driven destructive-bash-rules.json."""

    SHELL_TOOLS = {"bash", "sh", "terminal", "exec", "run_command", "run"}

    def __init__(self, rules_path: Optional[str] = None):
        self.rules_path = rules_path or self._default_rules_path()
        self.rules = self._load_rules()

    def _default_rules_path(self) -> str:
        candidates = [
            Path("hooks/destructive-bash-rules.json"),
            Path("vad/plugins/claude_code/hooks/destructive-bash-rules.json"),
            Path(__file__).parent / "destructive-bash-rules.json",
        ]
        for c in candidates:
            if c.exists():
                return str(c.as_posix())
        return "hooks/destructive-bash-rules.json"

    def _load_rules(self) -> Dict[str, List[Dict[str, str]]]:
        p = Path(self.rules_path)
        if not p.exists():
            return {"deny": [], "ask": []}
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"deny": [], "ask": []}

    def evaluate(self, tool_name: str, tool_input: Dict[str, Any]) -> GuardResult:
        if os.getenv("DESTRUCTIVE_BASH_GUARD", "on").lower() == "off":
            return GuardResult(
                allowed=True,
                decision=GuardDecision.ALLOW,
                reason="Escape hatch active (DESTRUCTIVE_BASH_GUARD=off)",
                guard_name="DestructiveBashGuard",
                bypassed=True,
            )

        norm_name = tool_name.lower()
        if not any(s in norm_name for s in self.SHELL_TOOLS):
            return GuardResult(allowed=True, decision=GuardDecision.ALLOW, reason="Non-shell tool", guard_name="DestructiveBashGuard")

        cmd = tool_input.get("command") or tool_input.get("cmd") or tool_input.get("commandline") or ""
        if not cmd or not isinstance(cmd, str):
            return GuardResult(allowed=True, decision=GuardDecision.ALLOW, reason="No command string", guard_name="DestructiveBashGuard")

        # 1. Deny tier check
        for rule in self.rules.get("deny", []):
            pat = rule.get("pattern", "")
            if pat and re.search(pat, cmd, re.IGNORECASE):
                desc = rule.get("description", "Prohibited destructive operation")
                return GuardResult(
                    allowed=False,
                    decision=GuardDecision.DENY,
                    reason=f"[DestructiveBashGuard DENY] Prohibited operation: {desc}. Command: {cmd}. Set DESTRUCTIVE_BASH_GUARD=off to bypass.",
                    guard_name="DestructiveBashGuard",
                )

        # 2. Ask tier check
        for rule in self.rules.get("ask", []):
            pat = rule.get("pattern", "")
            if pat and re.search(pat, cmd, re.IGNORECASE):
                desc = rule.get("description", "Risky shell operation")
                return GuardResult(
                    allowed=True,
                    decision=GuardDecision.ASK,
                    reason=f"[DestructiveBashGuard ASK] Risky shell operation requiring confirmation: {desc}. Command: {cmd}",
                    guard_name="DestructiveBashGuard",
                )

        return GuardResult(allowed=True, decision=GuardDecision.ALLOW, reason="Shell command permitted", guard_name="DestructiveBashGuard")


# ============================================================================
# 3. Read Privacy Guard
# ============================================================================

class ReadPrivacyGuard:
    """Blocks reading private sensitive files (.env*, *.pem, id_rsa*)."""

    BLOCKED_PATTERNS = [".env*", "*.pem", "id_rsa*", "*id_dsa*", "*id_ecdsa*", "*id_ed25519*"]
    READ_TOOLS = {"read", "view", "read_file", "view_file", "cat"}

    def evaluate(self, tool_name: str, tool_input: Dict[str, Any]) -> GuardResult:
        if os.getenv("READ_PRIVACY_GUARD", "on").lower() == "off":
            return GuardResult(
                allowed=True,
                decision=GuardDecision.ALLOW,
                reason="Escape hatch active (READ_PRIVACY_GUARD=off)",
                guard_name="ReadPrivacyGuard",
                bypassed=True,
            )

        paths: List[str] = []
        target = tool_input.get("path") or tool_input.get("file_path") or tool_input.get("target") or tool_input.get("absolutepath")
        if target:
            paths.append(str(target))

        cmd = tool_input.get("command") or tool_input.get("commandline")
        if cmd and isinstance(cmd, str):
            for part in cmd.split():
                paths.append(part)

        for p in paths:
            base = os.path.basename(p.strip("\"'"))
            for pattern in self.BLOCKED_PATTERNS:
                if fnmatch.fnmatch(base, pattern):
                    return GuardResult(
                        allowed=False,
                        decision=GuardDecision.DENY,
                        reason=f"[ReadPrivacyGuard Block] Access to privacy-sensitive file blocked: {p} matches pattern '{pattern}'. Set READ_PRIVACY_GUARD=off to bypass.",
                        guard_name="ReadPrivacyGuard",
                    )

        return GuardResult(allowed=True, decision=GuardDecision.ALLOW, reason="Privacy check satisfied", guard_name="ReadPrivacyGuard")


# ============================================================================
# 4. Agent Integrity Guard
# ============================================================================

class AgentIntegrityGuard:
    """Hash-checks custom subagent types against integrity registry."""

    def __init__(self, registry: Optional[IntegrityRegistry] = None, registry_path: str = "skills/registry.json"):
        self.registry = registry or IntegrityRegistry(registry_path)

    def evaluate(self, tool_name: str, tool_input: Dict[str, Any]) -> GuardResult:
        if os.getenv("AGENT_INTEGRITY_GUARD", "on").lower() == "off":
            return GuardResult(
                allowed=True,
                decision=GuardDecision.ALLOW,
                reason="Escape hatch active (AGENT_INTEGRITY_GUARD=off)",
                guard_name="AgentIntegrityGuard",
                bypassed=True,
            )

        agent_ref = (
            tool_input.get("agent_type")
            or tool_input.get("agent_name")
            or tool_input.get("agent_file")
            or tool_input.get("subagent")
            or tool_input.get("subagent_type")
            or ""
        )

        target_path = tool_input.get("path") or tool_input.get("file_path") or ""
        if not agent_ref and "agent" in str(target_path).lower():
            agent_ref = str(target_path)

        if not agent_ref:
            return GuardResult(allowed=True, decision=GuardDecision.ALLOW, reason="No agent reference", guard_name="AgentIntegrityGuard")

        candidates = [
            agent_ref,
            f"agents/{agent_ref}.yaml",
            f"agents/{agent_ref}.json",
            f"agents/{agent_ref}.py",
            f"vad/agents/{agent_ref}.py",
        ]

        found: Optional[Path] = None
        for c in candidates:
            p = Path(c)
            if p.exists() and p.is_file():
                found = p
                break

        if not found:
            return GuardResult(allowed=True, decision=GuardDecision.ALLOW, reason="Built-in or remote agent", guard_name="AgentIntegrityGuard")

        valid, err = self.registry.verify_safe(str(found.as_posix()))
        if not valid:
            return GuardResult(
                allowed=False,
                decision=GuardDecision.DENY,
                reason=f"[AgentIntegrityGuard DENY] Custom subagent definition {found} failed integrity check: {err}. Set AGENT_INTEGRITY_GUARD=off to bypass.",
                guard_name="AgentIntegrityGuard",
            )

        return GuardResult(allowed=True, decision=GuardDecision.ALLOW, reason="Subagent integrity verified", guard_name="AgentIntegrityGuard")


# ============================================================================
# 5. EIP Readiness Guard
# ============================================================================

class EIPReadinessGuard:
    """Blocks dispatches referencing an EIP unless status: ready and no open questions."""

    def evaluate(self, payload: Dict[str, Any]) -> GuardResult:
        if os.getenv("EIP_READINESS_GUARD", "on").lower() == "off":
            return GuardResult(
                allowed=True,
                decision=GuardDecision.ALLOW,
                reason="Escape hatch active (EIP_READINESS_GUARD=off)",
                guard_name="EIPReadinessGuard",
                bypassed=True,
            )

        tool_input = payload.get("tool_input", {}) if isinstance(payload.get("tool_input"), dict) else {}
        eip_ref = payload.get("eip") or tool_input.get("eip") or tool_input.get("eip_path") or tool_input.get("eip_file")
        if not eip_ref:
            return GuardResult(allowed=True, decision=GuardDecision.ALLOW, reason="No EIP bound", guard_name="EIPReadinessGuard")

        eip_dict = None
        if isinstance(eip_ref, dict):
            eip_dict = eip_ref
        elif isinstance(eip_ref, str):
            p = Path(eip_ref)
            if p.exists() and p.is_file():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        if p.suffix in (".yaml", ".yml"):
                            import yaml
                            eip_dict = yaml.safe_load(f)
                        else:
                            eip_dict = json.load(f)
                except Exception:
                    pass

        if not eip_dict:
            return GuardResult(
                allowed=False,
                decision=GuardDecision.DENY,
                reason=f"[EIPReadinessGuard DENY] Unparseable or missing EIP artifact: {eip_ref}",
                guard_name="EIPReadinessGuard",
            )

        status = str(eip_dict.get("status", "")).lower().strip()
        open_questions = eip_dict.get("open_questions", [])

        if status != "ready":
            return GuardResult(
                allowed=False,
                decision=GuardDecision.DENY,
                reason=f"[EIPReadinessGuard DENY] EIP is not ready for dispatch: status is '{status}' (must be 'ready'). Set EIP_READINESS_GUARD=off to bypass.",
                guard_name="EIPReadinessGuard",
            )

        if open_questions and len(open_questions) > 0:
            return GuardResult(
                allowed=False,
                decision=GuardDecision.DENY,
                reason=f"[EIPReadinessGuard DENY] EIP has {len(open_questions)} unresolved open question(s). All open questions must be resolved before builder dispatch. Set EIP_READINESS_GUARD=off to bypass.",
                guard_name="EIPReadinessGuard",
            )

        return GuardResult(allowed=True, decision=GuardDecision.ALLOW, reason="EIP readiness gate passed", guard_name="EIPReadinessGuard")
