"""Repo Descriptor Generator: Analyzes repositories and produces structured metadata cards for the routing corpus."""

from pathlib import Path
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import yaml


class RepoDescriptor(BaseModel):
    repo_name: str
    purpose: str
    language: str = "python"
    key_files: List[str] = Field(default_factory=list)
    routing_signals: List[str] = Field(default_factory=list)
    entrypoints: List[str] = Field(default_factory=list)
    tier: str = "standard"


class DescriptorGenerator:
    """Extracts architectural purpose and lexical routing signals from repo files."""

    SIGNAL_KEYWORDS = {
        "audio": ["sip", "rtp", "audio", "codec", "voice", "stream", "call"],
        "telemetry": ["statsd", "datadog", "metric", "audit", "otel", "trace", "log"],
        "auth": ["token", "jwt", "oauth", "saml", "oidc", "auth", "permission"],
        "billing": ["invoice", "stripe", "charge", "payment", "subscription"],
        "database": ["sql", "postgres", "redis", "mongo", "migration", "query"],
    }

    def generate_from_path(self, repo_dir: Path, repo_name: Optional[str] = None) -> RepoDescriptor:
        name = repo_name or repo_dir.name or "unknown-repo"
        purpose = "Enterprise service component"
        language = "python"
        key_files = []
        routing_signals = set()

        # 1. Inspect README
        readme_candidates = [repo_dir / "README.md", repo_dir / "readme.md", repo_dir / "README.txt"]
        readme_text = ""
        for r in readme_candidates:
            if r.exists():
                try:
                    readme_text = r.read_text(encoding="utf-8")
                    key_files.append(r.name)
                    # First non-header line as purpose
                    for line in readme_text.splitlines():
                        clean = line.strip().lstrip("#").strip()
                        if clean and len(clean) > 10 and not clean.startswith("!"):
                            purpose = clean
                            break
                except Exception:
                    pass
                break

        # 2. Detect language
        if (repo_dir / "pyproject.toml").exists() or (repo_dir / "setup.py").exists():
            language = "python"
            key_files.append("pyproject.toml" if (repo_dir / "pyproject.toml").exists() else "setup.py")
        elif (repo_dir / "package.json").exists():
            language = "javascript/typescript"
            key_files.append("package.json")
        elif (repo_dir / "go.mod").exists():
            language = "go"
            key_files.append("go.mod")
        elif (repo_dir / "Cargo.toml").exists():
            language = "rust"
            key_files.append("Cargo.toml")

        # 3. Detect routing signals from README and file names
        all_text = (readme_text + " " + " ".join([p.name for p in repo_dir.glob("*")])).lower()
        for domain, keywords in self.SIGNAL_KEYWORDS.items():
            for kw in keywords:
                if kw in all_text:
                    routing_signals.add(kw)
                    routing_signals.add(domain)

        if not routing_signals:
            routing_signals.update([name.lower(), language.lower()])

        entrypoints = []
        for ep in ["main.py", "app.py", "cli.py", "index.js", "main.go"]:
            if (repo_dir / ep).exists():
                entrypoints.append(ep)

        return RepoDescriptor(
            repo_name=name,
            purpose=purpose,
            language=language,
            key_files=key_files,
            routing_signals=sorted(list(routing_signals)),
            entrypoints=entrypoints,
            tier="standard",
        )

    def save_descriptor(self, descriptor: RepoDescriptor, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        data = descriptor.model_dump()
        output_path.write_text(yaml.dump(data, sort_keys=False), encoding="utf-8")
        return output_path
