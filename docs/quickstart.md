# VAD Quickstart

This guide will help you get started with the Verified Agentic Development (VAD) v0.1 reference implementation.

## Prerequisites & Environment

VAD is an entirely OS-agnostic (pure Python) implementation that runs natively on Windows, macOS, or Linux. 

- **Python 3.10+**
- **Open Policy Agent (OPA)**: OPA is a system dependency that must be installed on your native host path.
- **Docker / WSL (Optional)**: Docker and WSL can be used as an *optional* isolated convenience environment rather than a strict requirement. If you prefer not to install dependencies on your host, you can use the provided Dockerfile.

## Initializing an Executable Intent Package (EIP)

EIPs are the core artifacts in VAD. To initialize a new EIP:

```bash
vad eip init --name my-feature
```

This will create an `eip.yaml` (or `eip.json`) based on the `schemas/eip.schema.json` specification.

## Validating an EIP

Before implementation, validate your EIP to ensure it meets all structural and policy requirements:

```bash
vad eip validate my-feature/eip.yaml
```

## Running Tests

VAD integrates with standard testing frameworks to verify proof obligations. Once you have implemented the logic, you can verify it using `pytest`:

```bash
pytest
```
