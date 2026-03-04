Verified Agentic Development (VAD)
Reference Architecture — Maturity Model View
> For the full maturity model narrative, see [maturity_model.md](maturity_model.md).
Level 1–2: Structured + Bounded Agentic Development

```mermaid
flowchart LR
    A[Intent / EIP] --> B[Proof Planning]
    B --> C[Builder Agent]
    C --> D[Code / Artifacts]
    D --> E[Verifier Agent]
    E --> F{Proof Obligations Met?}
    F -->|No| C
    F -->|Yes| G[CI/CD Gate]
    G --> H[Progressive Release]
    H --> I[Telemetry]
    I --> J[Invariant Updates]
    J --> A
```

Characteristics

Human-defined EIP (Executable Intent Package)
Builder agent constrained by proof plan
Independent verifier agent
Progressive release with telemetry feedback
Single memory scope (project-level)
Limited model routing
This stage introduces control loops but autonomy remains bounded.

Level 3: Multi-Agent Verified Orchestration

```mermaid
flowchart TB
    A1[Intent / EIP] --> P[Planner Agent]

    P --> B1[Builder Agent - Code]
    P --> B2[Builder Agent - Tests]
    P --> B3[Builder Agent - Docs]

    B1 --> S[Synthesis Agent]
    B2 --> S
    B3 --> S

    S --> V[Verifier Swarm]
    V --> G{Verification Gate}

    G -->|Fail| P
    G -->|Pass| R[Controlled Release]

    R --> T[Telemetry + SLOs]
    T --> I[Invariant & Risk Register Updates]
    I --> A1
```

Additions at this Level

Planner agent decomposes work
Specialized builder agents
Synthesis agent merges outputs
Verifier swarm (security, property, performance)
Risk-tiered release gating
Economic routing layer (model tiering)
Autonomy increases — but governance hardens.    

Level 4: Enterprise Agentic Operating System

```mermaid
flowchart LR

    subgraph Governance Plane
        GP1[Policy-as-Code Engine]
        GP2[Model Routing & Budget Control]
        GP3[Agent Identity & Capability Registry]
        GP4[Proof & Evidence Store]
    end

    subgraph Memory Plane
        M1[Ephemeral Working Memory]
        M2[Project Memory]
        M3[Evidence Memory]
        M4[Telemetry Memory]
        M5[Org Knowledge Memory]
    end

    subgraph Agent Plane
        A1[Planner]
        A2[Builder Swarm]
        A3[Verifier Swarm]
        A4[Economic Router]
        A5[Telemetry Optimizer]
    end

    subgraph Execution Plane
        X1[Sandbox / Runtime]
        X2[CI/CD]
        X3[Feature Flags]
        X4[Production Systems]
    end

    A1 --> A2
    A2 --> A3
    A3 --> X2
    X2 --> X3
    X3 --> X4
    X4 --> M4
    M4 --> A5
    A5 --> A1

    GP1 -.-> A2
    GP2 -.-> A4
    GP3 -.-> A1
    GP4 -.-> X2

    M2 -.-> A1
    M1 -.-> A2
    M3 -.-> GP4
```

Architectural Layers Explained
1. Governance Plane

The control layer.
Policy-as-Code enforcement
Model routing and cost ceilings
Agent capability registry
Cryptographic proof bundles
Risk-tier classification

Nothing executes outside this envelope.

2. Memory Plane

Stratified memory model.
Working memory (session scoped)
Project memory (architectural context)
Evidence memory (proof + attestations)
Telemetry memory (runtime feedback)
Organizational knowledge memory

All memory access is mediated and logged.

3. Agent Plane

Specialized autonomous components.
Planner (intent decomposition)
Builder swarm (implementation)
Verifier swarm (adversarial + invariant testing)
Economic router (model + token governance)
Telemetry optimizer (post-release learning)

Separation of duties prevents self-approval bias.

4. Execution Plane

Where code meets reality.
Sandboxed runtime
CI/CD verification gates
Progressive release
Production systems

Telemetry closes the loop.

Maturity Progression (Condensed View)

```mermaid
flowchart LR
    L0[Assisted Coding]
    L1[Structured Intent + TDD]
    L2[Bounded Agentic Loops]
    L3[Multi-Agent Verified Orchestration]
    L4[Enterprise Agentic OS]

    L0 --> L1 --> L2 --> L3 --> L4
```

At low maturity:
AI accelerates output.

At high maturity:
AI operates inside a governed control system.

The transformation is not about automation.

It’s about shifting from:
Feature throughput
to:
Stable, verified, adaptive system evolution.