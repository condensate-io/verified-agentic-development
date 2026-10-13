import json
from pathlib import Path
import tempfile
import pytest

from vad.memory.skill_ledger import SkillLedger, SkillLedgerEntry, SkillOutcome
from vad.memory.ledger_retriever import LedgerRetriever
from vad.plugins.claude_code.hooks.post_tool_use import PostToolUseHook
from vad.plugins.claude_code.hooks.pre_tool_use import PreToolUseHook
from vad.plugins.claude_code.hooks.session_start import SessionStartHook


def test_ledger_hooks_end_to_end_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger_path = str(Path(tmpdir) / "skill_ledger.jsonl")
        ledger = SkillLedger(ledger_path=ledger_path)
        retriever = LedgerRetriever(ledger=ledger)

        post_hook = PostToolUseHook(skill_ledger=ledger)
        pre_hook = PreToolUseHook(ledger_retriever=retriever)
        session_hook = SessionStartHook(retriever=retriever)

        # 1. Simulate initial tool use (success)
        post_hook.process({
            "session_id": "sess-1",
            "agent_id": "builder",
            "tool_name": "data_fetcher",
            "tool_input": {"skill_name": "data_fetcher", "target": "cache"},
            "tool_output": "Data cached successfully",
            "exit_code": 0,
            "duration_ms": 150.0,
            "repo": "voice-agentic",
        })

        # Failure rate is 0/1 = 0%
        pre_res = pre_hook.process({
            "tool_name": "data_fetcher",
            "tool_input": {"skill_name": "data_fetcher"},
            "repo": "voice-agentic",
        })
        assert pre_res.permissionDecision == "allow"
        assert pre_res.ledger_context is None

        # 2. Simulate 2 subsequent failures of data_fetcher
        post_hook.process({
            "session_id": "sess-1",
            "agent_id": "builder",
            "tool_name": "data_fetcher",
            "tool_input": {"skill_name": "data_fetcher"},
            "tool_output": "Network timeout after 30s",
            "exit_code": 1,
            "duration_ms": 30000.0,
            "repo": "voice-agentic",
        })

        post_hook.process({
            "session_id": "sess-2",
            "agent_id": "builder",
            "tool_name": "data_fetcher",
            "tool_input": {"skill_name": "data_fetcher"},
            "tool_output": "Rate limit 429 exceeded",
            "exit_code": 1,
            "duration_ms": 2500.0,
            "repo": "voice-agentic",
        })

        # Failure rate is now 2/3 = 66.7% (>= 50%)
        # 3. PreToolUse should now surface a warning in ledger_context
        pre_res_after_failures = pre_hook.process({
            "tool_name": "data_fetcher",
            "tool_input": {"skill_name": "data_fetcher"},
            "repo": "voice-agentic",
        })
        assert pre_res_after_failures.permissionDecision == "allow"
        assert pre_res_after_failures.ledger_context is not None
        assert "high historical failure rate" in pre_res_after_failures.ledger_context
        assert "66.7%" in pre_res_after_failures.ledger_context

        # 4. SessionStartHook should now inject historical caveat memory for the repository
        session_res = session_hook.process({
            "session_id": "sess-3",
            "repo": "voice-agentic",
        })
        assert "Historical Skill Execution Memory & Known Caveats" in session_res.injected_context
        assert "**Skill `data_fetcher`**: (2/3 failures)" in session_res.injected_context
        assert "Network timeout" in session_res.injected_context or "Rate limit" in session_res.injected_context
