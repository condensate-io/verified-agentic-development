# Retrospective Loop (`vad eip retro`)

The Retrospective Loop is the autonomous learning component of the Verified Agentic Development (VAD) framework.

## The Autonomous Learning Loop

In a control-system model, telemetry and verification outcomes must be fed back into the system to improve future iterations. The autonomous learning loop analyzes the successes and failures of an EIP implementation.

## Using `vad eip retro`

After an EIP has completed its verification and deployment phases, run the retro command to synthesize learnings:

```bash
vad eip retro --eip my-feature/eip.yaml
```

This command will:
1. Analyze test results, invariant breaches, and execution logs.
2. Update the `MemoryScope.RETROSPECTIVE` storage.
3. Suggest new constraints or invariant refinements for future EIPs.
