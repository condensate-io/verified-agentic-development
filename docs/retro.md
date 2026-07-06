# Retrospective Loop (`vad eip retro`)

The Retrospective Loop is the autonomous learning component of the Verified Agentic Development (VAD) framework.

## The Autonomous Learning Loop

In a control-system model, telemetry and verification outcomes must be fed back into the system to improve future iterations. The autonomous learning loop analyzes the successes and failures of an EIP implementation.

## Using `vad eip retro`

After an EIP has completed its verification and deployment phases, run the retro command against an evidence bundle file:

```bash
vad eip retro path/to/evidence-bundle.json
```

This command will:
1. Analyze test results, invariant breaches, and execution logs.
2. Update the `MemoryScope.RETROSPECTIVE` storage.
3. Print synthesized learnings for future EIPs.

Structured proposal generation and durable retrospective memory are tracked in `implementation_tracker_2606.md`.
