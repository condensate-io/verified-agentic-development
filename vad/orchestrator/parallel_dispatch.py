"""Parallel Dispatch: Fans out multi-domain requests to concurrent agent runners and merges results."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field
from vad.orchestrator.dep_graph import DependencyGraph, TaskNode, TaskStatus


class SubtaskSpec(BaseModel):
    id: str
    domain: str
    prompt: str
    assigned_runner: str = "claude"
    timeout_seconds: float = 60.0


class SubtaskResult(BaseModel):
    subtask_id: str
    runner: str
    domain: str
    output: str
    exit_code: int = 0
    success: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ParallelDispatchResult(BaseModel):
    dispatch_id: str
    overall_prompt: str
    subtask_results: List[SubtaskResult] = Field(default_factory=list)
    merged_output: str
    all_succeeded: bool = True


class ParallelDispatcher:
    """Classifies multi-domain queries, executes subtasks in parallel tranches, and merges outputs."""

    def __init__(self, graph: Optional[DependencyGraph] = None):
        self.graph = graph

    def decompose(
        self, prompt: str, domain_rules: Optional[Dict[str, List[str]]] = None
    ) -> List[SubtaskSpec]:
        """Decomposes a compound prompt into domain-specific subtasks."""
        rules = domain_rules or {
            "security": ["auth", "jwt", "oauth", "password", "crypto", "token"],
            "data": ["database", "sql", "migration", "postgres", "redis", "schema"],
            "telemetry": ["metrics", "monitoring", "alert", "prometheus", "grafana", "slo"],
            "signaling": ["sip", "sdp", "rtp", "voip", "opensips", "webrtc"],
        }

        tokens = set(prompt.lower().split())
        matched_domains = []

        for domain, keywords in rules.items():
            if any(k in tokens or any(k in t for t in tokens) for k in keywords):
                matched_domains.append(domain)

        if not matched_domains:
            matched_domains = ["general"]

        subtasks = []
        for i, dom in enumerate(matched_domains, 1):
            subtasks.append(
                SubtaskSpec(
                    id=f"dispatch-sub-{i}-{dom}",
                    domain=dom,
                    prompt=f"[{dom.upper()} Domain Scope]: {prompt}",
                    assigned_runner="claude",
                )
            )
        return subtasks

    def dispatch(
        self,
        dispatch_id: str,
        prompt: str,
        subtasks: List[SubtaskSpec],
        runner_fn: Callable[[SubtaskSpec], SubtaskResult],
        max_workers: int = 4,
    ) -> ParallelDispatchResult:
        results: List[SubtaskResult] = []

        # If graph provided, register subtasks and synthetic merge node
        merge_task_id = f"{dispatch_id}-merge"
        if self.graph:
            subtask_ids = [st.id for st in subtasks]
            for st in subtasks:
                self.graph.add_task(TaskNode(id=st.id, title=f"Parallel Subtask: {st.domain}"))
                self.graph.lock(st.id, f"runner-{st.assigned_runner}")
            # Add synthetic merge task
            self.graph.add_task(
                TaskNode(
                    id=merge_task_id,
                    title=f"Merge Dispatch {dispatch_id}",
                    dependencies=subtask_ids,
                )
            )

        # Concurrently execute subtasks
        with ThreadPoolExecutor(max_workers=min(len(subtasks), max_workers)) as executor:
            future_to_spec = {executor.submit(runner_fn, spec): spec for spec in subtasks}
            for future in as_completed(future_to_spec):
                spec = future_to_spec[future]
                try:
                    res = future.result()
                    results.append(res)
                    if self.graph:
                        self.graph.complete(spec.id)
                except Exception as exc:
                    err_res = SubtaskResult(
                        subtask_id=spec.id,
                        runner=spec.assigned_runner,
                        domain=spec.domain,
                        output=f"Error executing subtask: {str(exc)}",
                        exit_code=1,
                        success=False,
                    )
                    results.append(err_res)
                    if self.graph:
                        self.graph.fail(spec.id, str(exc))

        # Sort results deterministically by subtask ID
        results.sort(key=lambda r: r.subtask_id)
        all_succeeded = all(r.success for r in results)

        # Merge outputs
        merged_blocks = [
            f"### Domain: {r.domain} (Runner: {r.runner}) - {'SUCCESS' if r.success else 'FAILED'}\n{r.output}"
            for r in results
        ]
        merged_output = f"# Merged Dispatch Synthesis [{dispatch_id}]\n\n" + "\n\n".join(merged_blocks)

        if self.graph and all_succeeded:
            self.graph.lock(merge_task_id, "synthesis-agent")
            self.graph.complete(merge_task_id)

        return ParallelDispatchResult(
            dispatch_id=dispatch_id,
            overall_prompt=prompt,
            subtask_results=results,
            merged_output=merged_output,
            all_succeeded=all_succeeded,
        )
