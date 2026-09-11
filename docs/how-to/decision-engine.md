# How-To: Configure and Operate the VAD Decision Engine

The **VAD Decision Engine** provides formal routing, gatekeeping, and confidence-calibrated dispatch for human asks, engineering intent, and architectural changes.

---

## 1. Overview of the Decision Engine

```
       ┌──────────────────────────────────────┐
       │             Incoming Ask             │
       └──────────────────┬───────────────────┘
                          │
                          ▼
       ┌──────────────────────────────────────┐
       │     Definition of Ready (DoR) Gate   │
       │    (Acceptance Criteria, Scope, Risk) │
       └──────────────────┬───────────────────┘
                          │ (Pass)
                          ▼
       ┌──────────────────────────────────────┐
       │          Decision Catalog            │
       │  (Available Harnesses & Capabilities)│
       └──────────┬────────────────┬──────────┘
                  │                │
       ┌──────────▼─────────┐   ┌──▼─────────────────┐
       │   Lexical Router   │   │  Semantic Router   │
       │     (BM25 / KW)    │   │  (Embeddings / Sim)│
       └──────────┬─────────┘   └──┬─────────────────┘
                  │                │
                  └───────┬────────┘
                          ▼
       ┌──────────────────────────────────────┐
       │        Hybrid Router (RRF)           │
       │    (Reciprocal Rank Fusion)          │
       └──────────────────┬───────────────────┘
                          │
                          ▼
       ┌──────────────────────────────────────┐
       │     ECE Calibration & Threshold      │
       │  Confidence >= Threshold ?           │
       ├──────────────────┬───────────────────┤
       │ Yes: Dispatch    │ No: Operator TUI  │
       │ to Target Runner │ Escalation        │
       └──────────────────┴───────────────────┘
```

---

## 2. Definition of Ready (DoR) Gating

Before any task or EIP is submitted for execution, it must pass the **Definition of Ready (DoR)** gate. The DoR gate enforces that work items possess clear acceptance criteria, test strategies, scope boundaries, and risk classifications.

### Python API Example
```python
from vad.decision.dor_gate import DoRGate, DoRItem

gate = DoRGate()

item = DoRItem(
    title="Implement Multi-Harness Projector",
    acceptance_criteria=["Must project to Claude, Cursor, Copilot", "All tests pass in Docker"],
    scope=["vad/harness/", "tests/test_projector.py"],
    risk_level="medium",
    test_strategy="Automated unit tests + contract verification"
)

result = gate.evaluate(item)
if not result.passed:
    print(f"DoR Gate Failed: {result.violations}")
else:
    print("DoR Passed. Proceeding to routing.")
```

---

## 3. Managing the Decision Catalog

The Decision Catalog (`schemas/decision_catalog.yaml`) lists available runners (Claude Code, Cursor, Copilot, Codex, Aider), their capabilities, latency profiles, cost multipliers, and supported language domains.

```yaml
version: "1.0"
harnesses:
  claude_code:
    display_name: "Claude Code"
    tier: "frontier"
    capabilities: ["architecture", "refactoring", "multi-file", "complex-logic"]
    cost_per_token: 0.000015
    context_window: 200000
  cursor:
    display_name: "Cursor"
    tier: "ide"
    capabilities: ["inline-edit", "single-file", "fast-loop"]
    cost_per_token: 0.000008
    context_window: 128000
  aider:
    display_name: "Aider"
    tier: "cli"
    capabilities: ["git-focused", "diff-review", "commit-authoring"]
    cost_per_token: 0.000005
    context_window: 64000
```

---

## 4. Configuring Routers (Lexical, Semantic, Hybrid)

VAD combines lexical keyword matching (BM25) and semantic vector similarity into a Reciprocal Rank Fusion (RRF) router:

```python
from vad.decision.catalog import DecisionCatalog
from vad.decision.router import DecisionRouter

catalog = DecisionCatalog.load_default()
router = DecisionRouter(catalog, threshold=0.75)

decision = router.route(
    intent="Refactor authentication middleware to support SAML 2.0 and RBAC",
    context={"criticality": "high", "lines_estimated": 400}
)

print(f"Selected Harness: {decision.selected_harness}")
print(f"Routing Confidence: {decision.confidence:.3f}")
print(f"Routed to Operator? {decision.requires_operator}")
```

---

## 5. Expected Calibration Error (ECE) Evaluation

To prevent overconfident routing mistakes by smaller models, VAD integrates Expected Calibration Error (ECE) monitoring and temperature scaling:

```python
from vad.decision.calibration import ECEEvaluator

evaluator = ECEEvaluator(num_bins=10)
# Feed prediction confidences and empirical outcomes
evaluator.add_observation(confidence=0.92, correct=True)
evaluator.add_observation(confidence=0.85, correct=False)

ece_score = evaluator.compute_ece()
print(f"Current ECE Score: {ece_score:.4f}")
if ece_score > 0.15:
    print("Warning: Decision engine is miscalibrated; applying temperature scaling.")
```
