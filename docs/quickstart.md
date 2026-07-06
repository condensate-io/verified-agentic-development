# VAD Quickstart

This guide will help you get started with the Verified Agentic Development (VAD) v0.1 reference implementation.

## Prerequisites and Environment

VAD is an entirely OS-agnostic (pure Python) implementation that runs natively on Windows, macOS, or Linux. 

- **Python 3.10+**
- **Open Policy Agent (OPA)**: OPA is a system dependency that must be installed on your native host path.
- **Docker / WSL**: For isolated local verification, use the provided Dockerfile from WSL so Python, pytest, and OPA stay out of the host environment.

## Executable Intent Packages

EIPs are the core artifacts in VAD. The current reference implementation includes a sample EIP at `examples/eip/sample.yaml`.

```bash
vad eip validate examples/eip/sample.yaml
```

Create a new EIP template:

```bash
vad eip init --name sample-change --out examples/eip/generated.yaml
```

Normalize an EIP for stable review:

```bash
vad eip normalize examples/eip/sample.yaml --out normalized.yaml
```

Compare two EIPs:

```bash
vad eip diff examples/eip/sample.yaml normalized.yaml --json
```

The `init`, `normalize`, `validate`, and `diff` command forms above are covered by CLI tests. Paths are illustrative when they write new files.

## Ask Assessment And Proof Mapping

Assess an ask from a text file:

```bash
vad ask assess ask.txt --out assessment.json
```

Create an EIP from an assessment:

```bash
vad eip init --name assessed-change --from-assessment assessment.json --out eip.yaml
```

Map proof obligations:

```bash
vad proof map eip.yaml --out proof-plan.yaml
```

Run the bounded VAD loop:

```bash
vad loop run eip.yaml proof-plan.yaml --out run-evidence.json
```

These command forms are covered by CLI tests; input file names are illustrative.

## Evidence And Effort

Inspect typed run evidence:

```bash
vad evidence inspect run-evidence.json
```

Score implementation effort using MEES:

```bash
vad effort score --type feature --readable --warn-only
```

These command forms are covered by CLI tests; evidence and Git state inputs are illustrative.

## Running Retrospective Analysis

Run retrospective analysis against an evidence bundle file:

```bash
vad eip retro path/to/evidence-bundle.json
```

This command form is covered by CLI tests; the evidence path is illustrative.

## Running Tests

Run tests inside disposable Docker from WSL:

```powershell
wsl -e bash -lc "cd /mnt/c/LocalProjects/condensate-arch-docs/verified-agentic-development && docker build -t vad-test:local . && docker run --rm vad-test:local"
```
