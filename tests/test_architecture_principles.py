"""Architecture principle docs and static authority/import boundary tests."""

import ast
import re
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_ARCH_ROOT = REPO_ROOT / "docs" / "architecture"
VAD_ROOT = REPO_ROOT / "vad"

CORE_ISOLATED_PACKAGES = ("verify", "policy", "evidence", "release")
FORBIDDEN_MEMORY_SDKS = ("condensate", "mem0", "graphiti", "qdrant", "qdrant_client")
FORBIDDEN_LLM_SDKS = ("openai", "anthropic", "google.genai", "vertexai")
FORBIDDEN_CORE_IMPORTS = FORBIDDEN_MEMORY_SDKS + FORBIDDEN_LLM_SDKS

AUTHORITATIVE_TABLES = (
    "run_evidence",
    "approvals",
    "work_items",
    "diff_proposals",
    "diff_apply_records",
    "operator_intents",
)

_SQL_DML_PATTERNS = tuple(
    re.compile(
        rf"""
        (?:
            \bINSERT\s+(?:OR\s+\w+\s+)?INTO\s+
            | \bUPDATE\s+(?:OR\s+\w+\s+)?
            | \bDELETE\s+FROM\s+
        )
        [`"\[]?(?:\w+\.)?{table}\b[`"\]]?
        """,
        re.IGNORECASE | re.VERBOSE,
    )
    for table in AUTHORITATIVE_TABLES
)


def _matches_module(imported_name: str, forbidden: str) -> bool:
    return imported_name == forbidden or imported_name.startswith(f"{forbidden}.")


def find_forbidden_imports(
    source: str,
    forbidden_modules: Iterable[str],
) -> list[str]:
    """Return any forbidden module imports found via Python AST inspection."""
    tree = ast.parse(source)
    forbidden = tuple(forbidden_modules)
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for mod in forbidden:
                    if _matches_module(alias.name, mod):
                        violations.append(f"import {alias.name} (forbidden: {mod})")
        elif isinstance(node, ast.ImportFrom):
            base_module = node.module or ""
            for mod in forbidden:
                if base_module and _matches_module(base_module, mod):
                    violations.append(f"from {base_module} import ... (forbidden: {mod})")
                    continue
                for alias in node.names:
                    candidate = f"{base_module}.{alias.name}" if base_module else alias.name
                    if _matches_module(candidate, mod):
                        violations.append(
                            f"from {base_module} import {alias.name} (forbidden: {mod})"
                        )

    return violations


def find_memory_authority_violations(source: str) -> list[str]:
    """Return ServerStore imports or authoritative-table SQL DML found in source."""
    tree = ast.parse(source)
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "vad.server.db.store" or alias.name.endswith(".ServerStore"):
                    violations.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            imported_names = [alias.name for alias in node.names]
            if "ServerStore" in imported_names or module_name == "vad.server.db.store":
                violations.append(
                    f"from {module_name} import {', '.join(imported_names)}"
                )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for pattern in _SQL_DML_PATTERNS:
                if pattern.search(node.value):
                    violations.append(
                        f"SQL DML targeting authoritative table in literal: {node.value!r}"
                    )

    for pattern in _SQL_DML_PATTERNS:
        if pattern.search(source):
            violations.append(
                f"SQL DML pattern matched source: {pattern.pattern.strip()}"
            )

    return violations


def test_principles_doc_exists_and_codifies_architecture() -> None:
    principles_path = DOCS_ARCH_ROOT / "principles.md"
    assert principles_path.is_file(), f"Missing {principles_path}"

    text = principles_path.read_text(encoding="utf-8")
    lowered = text.lower()

    expected_principles = [
        "Model-agnostic",
        "Host-agnostic",
        "Memory-agnostic",
        "Local-first",
        "Evidence-first",
        "Policy-driven",
        "Recoverable",
        "Extensible",
        "Verifiable",
    ]
    for principle in expected_principles:
        assert principle in text, f"Missing principle {principle!r} in principles.md"

    for layer_marker in [
        "Agent Execution Hosts",
        "Codex",
        "Claude Code",
        "Cursor",
        "VAD Control Plane",
        "Memory Provider Interface",
        "Condensate Core",
        "Mem0",
        "Graphiti",
    ]:
        assert layer_marker in text, f"Missing 3-layer marker {layer_marker!r}"

    for positioning_marker in [
        "Scope A",
        "Verified Software Delivery Platform",
        "Scope B",
        "General Autonomous Agent Control System",
        "Stage 01",
        "Stage 02",
        "Stage 03",
        "Stage 04",
    ]:
        assert positioning_marker in text, (
            f"Missing positioning/stage marker {positioning_marker!r}"
        )

    assert "nullmemoryprovider" in lowered


def test_authority_boundaries_doc_exists_and_codifies_ownership_and_invariants() -> None:
    boundaries_path = DOCS_ARCH_ROOT / "authority-boundaries.md"
    assert boundaries_path.is_file(), f"Missing {boundaries_path}"

    text = boundaries_path.read_text(encoding="utf-8")
    lowered = text.lower()

    vad_owned_items = [
        "Executable Intent Packages",
        "Proof obligations",
        "Verification results",
        "Policy decisions",
        "Approval records",
        "Execution state",
        "Authoritative evidence ledger",
    ]
    for item in vad_owned_items:
        assert item in text, f"Missing VAD-owned item {item!r}"

    memory_owned_items = [
        "Historical knowledge",
        "Semantic retrieval",
        "Lessons learned",
        "Context consolidation",
    ]
    for item in memory_owned_items:
        assert item in text, f"Missing memory-owned item {item!r}"

    for invariant_phrase in [
        "alter verification results",
        "grant approvals",
        "override policy decisions",
        "mutate the authoritative evidence ledger",
        "observed facts",
        "inferred lessons",
        "human review",
    ]:
        assert invariant_phrase in lowered, (
            f"Missing security invariant phrase {invariant_phrase!r}"
        )


def test_core_packages_have_zero_vendor_llm_or_external_memory_imports() -> None:
    findings: list[str] = []

    for pkg in CORE_ISOLATED_PACKAGES:
        pkg_dir = VAD_ROOT / pkg
        assert pkg_dir.is_dir(), f"Expected package directory {pkg_dir}"
        for py_file in sorted(pkg_dir.rglob("*.py")):
            source = py_file.read_text(encoding="utf-8")
            violations = find_forbidden_imports(source, FORBIDDEN_CORE_IMPORTS)
            for violation in violations:
                findings.append(f"{py_file.relative_to(REPO_ROOT)}: {violation}")

    assert findings == []


def test_memory_package_never_imports_server_store_or_writes_authoritative_tables() -> None:
    memory_dir = VAD_ROOT / "memory"
    assert memory_dir.is_dir(), f"Expected package directory {memory_dir}"

    findings: list[str] = []
    for py_file in sorted(memory_dir.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        violations = find_memory_authority_violations(source)
        for violation in violations:
            findings.append(f"{py_file.relative_to(REPO_ROOT)}: {violation}")

    assert findings == []


def test_static_boundary_scanners_catch_synthetic_violations() -> None:
    for sample in [
        "import openai",
        "from anthropic import Anthropic",
        "from google import genai",
        "import google.genai as genai",
        "import vertexai",
        "from condensate.client import Client",
        "import mem0",
        "from graphiti import Graphiti",
        "import qdrant",
        "import qdrant_client",
    ]:
        assert find_forbidden_imports(sample, FORBIDDEN_CORE_IMPORTS), (
            f"Expected violation for {sample!r}"
        )

    for sample in [
        "from vad.server.db.store import ServerStore",
        "import vad.server.db.store",
        'sql = "INSERT INTO approvals (id) VALUES (?)"',
        'sql = "UPDATE work_items SET status = ?"',
        'sql = "DELETE FROM run_evidence WHERE id = ?"',
        'sql = "INSERT INTO diff_proposals (id) VALUES (?)"',
        'sql = "UPDATE diff_apply_records SET status = ?"',
        'sql = "DELETE FROM operator_intents WHERE id = ?"',
    ]:
        assert find_memory_authority_violations(sample), (
            f"Expected authority violation for {sample!r}"
        )
