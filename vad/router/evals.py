"""Routing Evaluation Discipline: Evals and held-out routing benchmark generator for model routing."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import random
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field


class RoutingScenario(BaseModel):
    task_id: str
    repo: str
    purpose: str  # "build" | "verification" | "formatting" | "refactor" | "security_audit"
    risk_tier: str  # "low" | "moderate" | "high" | "regulated"
    estimated_tokens: int = Field(ge=0)
    budget_ceiling: float = Field(ge=0.0)
    expected_tier: str  # "tier1" | "tier2"
    split: str = "held_out"  # "train" | "held_out"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RoutingEvalResult(BaseModel):
    total_scenarios: int
    held_out_scenarios: int
    correct_tier_routes: int
    accuracy: float
    policy_compliant_routes: int
    compliance_rate: float
    total_estimated_cost: float
    summary: str


class RoutingEvalDataset:
    """Manages synthetic and repository-derived routing benchmarks with held-out splits."""

    def __init__(self, scenarios: Optional[List[RoutingScenario]] = None):
        self.scenarios = scenarios or []

    @classmethod
    def generate_from_repo(
        cls,
        repo_path: str,
        held_out_ratio: float = 0.3,
        seed: int = 42,
    ) -> "RoutingEvalDataset":
        """Generates or regenerates routing benchmark scenarios dynamically based on repository files."""
        rng = random.Random(seed)
        p = Path(repo_path)

        scenarios: List[RoutingScenario] = []
        file_count = 0
        sensitive_files = []
        normal_files = []

        if p.exists():
            for root, _, files in os.walk(p):
                for f in files:
                    file_count += 1
                    rel_p = os.path.relpath(os.path.join(root, f), p).replace("\\", "/")
                    lower = rel_p.lower()
                    if any(term in lower for term in ("auth", "secret", "crypto", "token", "password", "guard", "security")):
                        sensitive_files.append(rel_p)
                    elif lower.endswith((".py", ".ts", ".js", ".go", ".rs")):
                        normal_files.append(rel_p)

        repo_name = p.name or "active-repo"

        # Generate low-risk formatting/linting tasks
        for idx in range(max(3, min(10, file_count // 5))):
            target = normal_files[idx % len(normal_files)] if normal_files else "src/app.py"
            scenarios.append(
                RoutingScenario(
                    task_id=f"task-fmt-{idx}",
                    repo=repo_name,
                    purpose="formatting",
                    risk_tier="low",
                    estimated_tokens=500,
                    budget_ceiling=1.0,
                    expected_tier="tier1",
                    metadata={"target_file": target},
                )
            )

        # Generate standard build/refactor tasks
        for idx in range(max(3, min(10, file_count // 4))):
            target = normal_files[idx % len(normal_files)] if normal_files else "src/module.py"
            scenarios.append(
                RoutingScenario(
                    task_id=f"task-build-{idx}",
                    repo=repo_name,
                    purpose="build",
                    risk_tier="moderate",
                    estimated_tokens=2500,
                    budget_ceiling=5.0,
                    expected_tier="tier1",
                    metadata={"target_file": target},
                )
            )

        # Generate high-risk verification / security audit tasks
        for idx in range(max(3, min(10, len(sensitive_files) + 3))):
            target = sensitive_files[idx % len(sensitive_files)] if sensitive_files else "src/security.py"
            scenarios.append(
                RoutingScenario(
                    task_id=f"task-sec-{idx}",
                    repo=repo_name,
                    purpose="verification",
                    risk_tier="high",
                    estimated_tokens=4000,
                    budget_ceiling=10.0,
                    expected_tier="tier2",
                    metadata={"target_file": target},
                )
            )

        # Partition into train vs held-out
        for s in scenarios:
            s.split = "held_out" if rng.random() < held_out_ratio else "train"

        # Guarantee at least 2 held-out scenarios
        if sum(1 for s in scenarios if s.split == "held_out") == 0 and scenarios:
            scenarios[0].split = "held_out"

        return cls(scenarios=scenarios)

    def get_held_out(self) -> List[RoutingScenario]:
        return [s for s in self.scenarios if s.split == "held_out"]

    def get_train(self) -> List[RoutingScenario]:
        return [s for s in self.scenarios if s.split == "train"]

    def save(self, filepath: str) -> None:
        p = Path(filepath)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump([s.model_dump() for s in self.scenarios], f, indent=2)

    @classmethod
    def load(cls, filepath: str) -> "RoutingEvalDataset":
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls([RoutingScenario(**item) for item in data])


class RoutingEvaluator:
    """Evaluates router logic against held-out benchmark datasets."""

    def __init__(self, dataset: RoutingEvalDataset):
        self.dataset = dataset

    def evaluate(self, route_fn: Optional[Any] = None) -> RoutingEvalResult:
        """Evaluates router decision accuracy on held-out scenarios."""
        held_out = self.dataset.get_held_out()
        if not held_out:
            return RoutingEvalResult(
                total_scenarios=len(self.dataset.scenarios),
                held_out_scenarios=0,
                correct_tier_routes=0,
                accuracy=1.0,
                policy_compliant_routes=0,
                compliance_rate=1.0,
                total_estimated_cost=0.0,
                summary="No held-out scenarios to evaluate.",
            )

        correct = 0
        compliant = 0
        total_cost = 0.0

        for sc in held_out:
            # If no custom router provided, evaluate policy baseline
            if route_fn:
                chosen_tier = route_fn(sc)
            else:
                # Standard VAD routing rule: high risk/verification -> tier2, else tier1
                if sc.risk_tier == "high" or sc.purpose == "verification":
                    chosen_tier = "tier2"
                else:
                    chosen_tier = "tier1"

            is_correct = (chosen_tier == sc.expected_tier)
            if is_correct:
                correct += 1

            # High risk tasks routed to tier1 violate policy
            is_compliant = not (sc.risk_tier == "high" and chosen_tier != "tier2")
            if is_compliant:
                compliant += 1

            cost_per_token = 0.00003 if chosen_tier == "tier2" else 0.000003
            total_cost += sc.estimated_tokens * cost_per_token

        acc = round(correct / len(held_out), 4)
        comp = round(compliant / len(held_out), 4)

        return RoutingEvalResult(
            total_scenarios=len(self.dataset.scenarios),
            held_out_scenarios=len(held_out),
            correct_tier_routes=correct,
            accuracy=acc,
            policy_compliant_routes=compliant,
            compliance_rate=comp,
            total_estimated_cost=round(total_cost, 4),
            summary=f"Held-out routing evaluation: accuracy={acc*100:.1f}%, compliance={comp*100:.1f}%, cost=${total_cost:.4f}",
        )
