"""CLI Orchestrator: Command-line orchestration loop reading and writing implementation_tracker.md."""

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional
from vad.orchestrator.admission import TaskAdmissionController
from vad.orchestrator.confirm_push import PushConfirmationController
from vad.orchestrator.dep_graph import DependencyGraph, TaskNode, TaskStatus
from vad.orchestrator.heartbeat import HeartbeatMonitor
from vad.orchestrator.staged_commit import StagedCommitManager


class TrackerMarkdownSync:
    """Parses and updates implementation_tracker.md table rows."""

    ROW_REGEX = re.compile(
        r"^\|\s*([A-Z0-9_-]+)\s*\|\s*([a-z_-]+)\s*\|\s*([^|]*)\|\s*([^|]*)\|\s*([^|]*)\|\s*([^|]*)\|\s*([^|]*)\|"
    )

    @classmethod
    def parse_tasks(cls, file_path: str) -> List[TaskNode]:
        p = Path(file_path)
        if not p.exists():
            return []

        tasks = []
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                match = cls.ROW_REGEX.match(line.strip())
                if match:
                    t_id = match.group(1).strip()
                    if t_id.lower() == "id" or set(t_id) <= {"-", "="} or not t_id:
                        continue
                    status_str = match.group(2).strip()
                    notes = match.group(7).strip()

                    # Extract dependencies from notes if present ("Depends on X, Y")
                    deps = []
                    dep_match = re.search(r"Depends on ([A-Z0-9_, -]+)", notes)
                    if dep_match:
                        raw_deps = dep_match.group(1).split(",")
                        for d in raw_deps:
                            clean_d = d.strip()
                            if clean_d and clean_d != "—":
                                deps.append(clean_d)

                    status = TaskStatus.READY
                    try:
                        status = TaskStatus(status_str)
                    except ValueError:
                        pass

                    tasks.append(
                        TaskNode(
                            id=t_id,
                            title=f"Task {t_id}",
                            dependencies=deps,
                            status=status,
                            metadata={"notes": notes},
                        )
                    )
        return tasks

    @classmethod
    def update_task_status(
        cls,
        file_path: str,
        task_id: str,
        new_status: str,
        agent: str = "orchestrator",
        note: str = "",
    ) -> bool:
        p = Path(file_path)
        if not p.exists():
            return False

        lines = []
        updated = False
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                match = cls.ROW_REGEX.match(line.strip())
                if match and match.group(1).strip() == task_id:
                    # Update row
                    notes = note if note else match.group(7).strip()
                    new_row = f"| {task_id} | {new_status:<6} | {agent:<12} | 13:00     | 13:05     | 2026-09-11   | {notes} |\n"
                    lines.append(new_row)
                    updated = True
                else:
                    lines.append(line)

        if updated:
            with open(p, "w", encoding="utf-8") as f:
                f.writelines(lines)
        return updated


class CLIOrchestrator:
    """Orchestrator service coordinating parallel task execution, heartbeat recovery, and status reporting."""

    def __init__(self, tracker_path: str = "planning_artifacts/implementation_tracker.md"):
        self.tracker_path = tracker_path
        self.graph = DependencyGraph()
        self.heartbeat_monitor = HeartbeatMonitor(self.graph)
        self.staged_manager = StagedCommitManager(self.graph)
        self.push_controller = PushConfirmationController(self.staged_manager)

    def load(self) -> None:
        tasks = TrackerMarkdownSync.parse_tasks(self.tracker_path)
        self.graph = DependencyGraph()
        self.heartbeat_monitor = HeartbeatMonitor(self.graph)
        for t in tasks:
            try:
                self.graph.add_task(t)
            except Exception:
                pass

    def get_status_summary(self) -> Dict[str, int]:
        self.load()
        counts = {"ready": 0, "locked": 0, "staged": 0, "done": 0, "failed": 0}
        for task in self.graph.all_tasks():
            counts[task.status.value] = counts.get(task.status.value, 0) + 1
        return counts

    def resume_crashed(self, timeout_seconds: float = 600.0) -> List[str]:
        self.load()
        expired = self.heartbeat_monitor.check_all()
        for t_id in expired:
            TrackerMarkdownSync.update_task_status(
                self.tracker_path, t_id, "ready", agent="—", note="Reset from crashed lock"
            )
        return expired

    def run_tick(self, max_concurrent: int = 4) -> List[str]:
        """Dispatches ready items in the current phase/tranche."""
        self.load()
        ready = self.graph.ready_items()
        dispatched = []
        for task in ready[:max_concurrent]:
            self.graph.lock(task.id, "runner-worker")
            TrackerMarkdownSync.update_task_status(self.tracker_path, task.id, "locked", agent="runner-worker")
            dispatched.append(task.id)
        return dispatched

    def confirm_staged_task(self, staged_id: str, operator_id: str = "human-operator") -> bool:
        res = self.push_controller.confirm_and_push(staged_id, operator_id=operator_id)
        return res.pushed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VAD CLI Orchestrator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_p = subparsers.add_parser("status", help="Show orchestrator DAG status")
    status_p.add_argument("--tracker", default="planning_artifacts/implementation_tracker.md")

    resume_p = subparsers.add_parser("resume", help="Resume and reset crashed locks")
    resume_p.add_argument("--tracker", default="planning_artifacts/implementation_tracker.md")

    run_p = subparsers.add_parser("run", help="Run ready tasks")
    run_p.add_argument("--tracker", default="planning_artifacts/implementation_tracker.md")

    confirm_p = subparsers.add_parser("confirm", help="Confirm staged task for push")
    confirm_p.add_argument("staged_id", help="ID of the staged commit to confirm")
    confirm_p.add_argument("--operator", default="operator-cli")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    orchestrator = CLIOrchestrator(tracker_path=getattr(args, "tracker", "planning_artifacts/implementation_tracker.md"))

    if args.command == "status":
        summary = orchestrator.get_status_summary()
        print("VAD Orchestrator DAG Status:")
        for k, v in summary.items():
            print(f"  {k.upper()}: {v}")
        return 0
    elif args.command == "resume":
        expired = orchestrator.resume_crashed()
        print(f"Reset {len(expired)} stale/crashed tasks back to ready: {expired}")
        return 0
    elif args.command == "run":
        dispatched = orchestrator.run_tick()
        print(f"Dispatched {len(dispatched)} ready tasks: {dispatched}")
        return 0
    elif args.command == "confirm":
        success = orchestrator.confirm_staged_task(args.staged_id, operator_id=args.operator)
        if success:
            print(f"Staged commit '{args.staged_id}' confirmed and pushed successfully.")
            return 0
        else:
            print(f"Failed to confirm/push staged commit '{args.staged_id}'.")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
