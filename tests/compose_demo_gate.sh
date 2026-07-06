#!/usr/bin/env bash
set -euo pipefail

docker compose --profile level4 up -d --build --force-recreate vad-control-plane

cleanup() {
    status=$?
    if [ "$status" -ne 0 ]; then
        docker compose --profile level4 ps
        docker compose --profile level4 logs --no-color vad-control-plane | tail -120
    fi
    docker compose --profile level4 down
    exit "$status"
}
trap cleanup EXIT

ready=0
for _ in {1..60}; do
    if curl -fsS http://127.0.0.1:8081/ready >/tmp/vad-compose-ready.json; then
        ready=1
        break
    fi
    sleep 1
done
test "$ready" -eq 1

curl -fsS http://127.0.0.1:8081/health >/tmp/vad-compose-health.json
curl -fsS http://127.0.0.1:8081/dashboard >/tmp/vad-compose-dashboard.json
curl -fsS 'http://127.0.0.1:8081/dashboard/replay?run_id=multi-client-simulator' >/tmp/vad-compose-replay.json

python3 - <<'PY'
import json
from pathlib import Path

health = json.loads(Path("/tmp/vad-compose-health.json").read_text())
ready = json.loads(Path("/tmp/vad-compose-ready.json").read_text())
dashboard = json.loads(Path("/tmp/vad-compose-dashboard.json").read_text())
replay = json.loads(Path("/tmp/vad-compose-replay.json").read_text())

expected = {
    "Codex",
    "Antigravity",
    "Claude Code",
    "VS Code",
    "Windsurf",
    "Cursor",
    "OpenCode",
    "Generic MCP/A2A",
}

assert health == {"status": "ok"}, health
assert ready == {"status": "ready"}, ready
assert {client["display_name"] for client in dashboard["active_clients"]} == expected
assert set(dashboard["client_counts"]) == expected
assert dashboard["event_timeline"]
assert set(dashboard["task_board_columns"]) == {"active", "blocked", "passed", "failed", "needs_human"}
assert replay["replay"]["run_id"] == "multi-client-simulator"
assert {event["run_id"] for event in replay["event_timeline"]} == {"multi-client-simulator"}
print("compose smoke ok")
PY

trap - EXIT
docker compose --profile level4 down
