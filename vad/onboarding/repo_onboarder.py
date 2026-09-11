"""Repo Onboarder: Orchestrates repository onboarding, corpus updating, agent scaffolding, and router re-indexing."""

import argparse
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
import yaml

from vad.decision.hybrid_router import HybridRouter
from vad.decision.lexical_router import SkillDescriptor
from vad.onboarding.descriptor_generator import DescriptorGenerator, RepoDescriptor


class RepoOnboarder:
    """Automates end-to-end repository onboarding into the governed VAD workspace."""

    def __init__(
        self,
        corpus_dir: str = "corpus",
        agents_dir: str = "agents",
        hybrid_router: Optional[HybridRouter] = None,
    ):
        self.corpus_dir = Path(corpus_dir)
        self.agents_dir = Path(agents_dir)
        self.hybrid_router = hybrid_router or HybridRouter()
        self.generator = DescriptorGenerator()
        self.corpus_dir.mkdir(parents=True, exist_ok=True)
        self.agents_dir.mkdir(parents=True, exist_ok=True)

    def onboard_repository(
        self,
        repo_path: Path,
        repo_name: Optional[str] = None,
        git_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        # 1. Generate descriptor
        descriptor = self.generator.generate_from_path(repo_path, repo_name=repo_name)
        name = descriptor.repo_name

        # 2. Save into corpus
        desc_file = self.corpus_dir / f"{name}.yaml"
        self.generator.save_descriptor(descriptor, desc_file)

        # 3. Scaffold agent and skill pairing
        agent_file = self.agents_dir / f"{name}_agent.yaml"
        agent_spec = {
            "name": f"{name}-agent",
            "role": f"Specialist Engineer for {name}",
            "description": descriptor.purpose,
            "target_repo": name,
            "git_url": git_url or f"https://git.internal/{name}.git",
            "routing_signals": descriptor.routing_signals,
            "allowed_tools": ["read", "pytest", "git_status", "edit"],
        }
        agent_file.write_text(yaml.dump(agent_spec, sort_keys=False), encoding="utf-8")

        # 4. Load full corpus and re-index hybrid router
        corpus_descriptors = self.load_all_corpus_descriptors()
        self.hybrid_router.index(corpus_descriptors)

        return {
            "repo_name": name,
            "status": "onboarded",
            "descriptor_file": str(desc_file),
            "agent_file": str(agent_file),
            "indexed_skill_count": len(corpus_descriptors),
            "routing_signals": descriptor.routing_signals,
        }

    def load_all_corpus_descriptors(self) -> List[SkillDescriptor]:
        descriptors: List[SkillDescriptor] = []
        if not self.corpus_dir.exists():
            return descriptors

        for p in self.corpus_dir.glob("*.yaml"):
            try:
                data = yaml.safe_load(p.read_text(encoding="utf-8"))
                if not data:
                    continue
                r_name = data.get("repo_name", p.stem)
                purpose = data.get("purpose", "Service component")
                signals = data.get("routing_signals", [])
                descriptors.append(
                    SkillDescriptor(
                        id=f"skill-{r_name}",
                        name=r_name,
                        description=purpose,
                        keywords=signals,
                        domain=signals[0] if signals else "general",
                        target_agent=f"{r_name}-agent",
                    )
                )
            except Exception:
                pass
        return descriptors


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Onboard repository into VAD governance")
    parser.add_argument("--dir", default=".", help="Local repository path")
    parser.add_argument("--name", help="Repository identifier name")
    parser.add_argument("--url", help="Remote Git clone URL")
    parser.add_argument("--corpus", default="corpus", help="Corpus storage directory")
    args = parser.parse_args(argv)

    onboarder = RepoOnboarder(corpus_dir=args.corpus)
    res = onboarder.onboard_repository(Path(args.dir), repo_name=args.name, git_url=args.url)
    print(f"Repository '{res['repo_name']}' successfully onboarded. Corpus indexed with {res['indexed_skill_count']} skills.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
