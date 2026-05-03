# Enterprise Agent Platform — Design Architecture

A reference architecture for a governed, observable, multi-surface agent platform. The design separates **governance**, **runtime**, **integration (gateways)**, and **observability** into distinct planes so each can evolve independently while remaining centrally controlled.

---

## 1. Architecture Overview

The platform is organized into four horizontal planes connected by a north–south control flow and east–west data flow:

| Plane | Components | Responsibility |
|---|---|---|
| **Governance & Protections** | Agent Registry, Agent Policy, Model Armor | Policy authoring, registration, prompt/response safety |
| **Client Surfaces** | Gemini Enterprise, Workspace, Custom Apps | Human and system entry points |
| **Agent Runtime** | Agent Identity, Model, Sessions/Memory | Reasoning, state, and execution |
| **Integration** | Agent Gateway (ingress + egress) | Brokered access to surfaces, tools, and peer agents |
| **Observability** | Unified Telemetry Router → Logs, Metrics, Traces | Evaluation and anomaly detection |

---

## 2. Component Responsibilities

### 2.1 AI Governance and Protections (Control Plane)

- **Agent Registry** — Source of truth for every registered agent: ID, owner, version, capabilities, allowed tools, and lifecycle state.
- **Agent Policy** — Declarative rules governing what agents *can* and *cannot* do (data scopes, tool allow-lists, rate limits, escalation paths).
- **Model Armor** — Inline protection layer for prompt-injection defense, PII redaction, jailbreak detection, and unsafe-output filtering on both inbound and outbound traffic.

> Governance applies vertically to every other plane (shown in the diagram as dashed green lines) — no traffic bypasses these controls.

### 2.2 Client Surfaces (Ingress)

- **Gemini Enterprise** — First-party assistant surface.
- **Workspace** — Productivity-suite integrations (mail, docs, calendar, drive).
- **Custom Apps** — Customer-built front-ends consuming the agent via API/SDK.

All surfaces enter the platform through the **left Agent Gateway**, never directly into the runtime.

### 2.3 Agent Runtime (Data Plane)

The execution kernel. Each agent instance is composed of three coupled subsystems:

- **Agent Identity** — Cryptographic identity used for authn/authz, audit, and downstream tool calls. Bound to the registry record.
- **Model** — The reasoning LLM (or model router). Stateless per call; receives prompt, context, and tool schemas.
- **Sessions / Memory** — Short-term session state plus long-term memory. Scoped to user × agent × tenant; encrypted at rest.

### 2.4 Agent Gateway (Integration)

A single logical component deployed twice — **ingress** (surfaces → runtime) and **egress** (runtime → external):

- **Ingress** — Authenticates the caller, attaches tenant context, enforces policy, and applies Model Armor before the runtime sees the request.
- **Egress** — Mediates outbound calls to:
  - **Tools** via **MCP** (Model Context Protocol)
  - **Other Agents** via **A2A** (Agent-to-Agent protocol)

Both directions emit telemetry to the observability plane.

### 2.5 Agent Observability

A **Unified Telemetry Router** fans out a single event stream into:

- **Logs** — Structured, per-step records (prompt, tool call, response).
- **Metrics** — Latency, cost, token usage, error rates, tool success.
- **Traces** — End-to-end span trees across surface → runtime → tool/agent.

Downstream consumers:

- **Evaluation** — Offline/online quality scoring and regression testing.
- **Anomaly Detection** — Drift, abuse, and safety-incident alerting.

---

## 3. Request Lifecycle (End-to-End Flow)

1. **Surface call** — User acts in Gemini Enterprise / Workspace / Custom App.
2. **Ingress Gateway** — Validates identity, resolves agent from Registry, checks Policy, runs Model Armor on input.
3. **Runtime** — Agent loads memory, calls Model, decides on tool/agent invocations.
4. **Egress Gateway** — Authorizes each outbound call; routes via MCP (tools) or A2A (agents).
5. **Response** — Model Armor scans output → Policy re-checks → response returned to surface.
6. **Telemetry** — Every hop emits logs/metrics/traces to the Unified Telemetry Router.
7. **Eval & Anomaly** — Async consumers score quality and flag anomalies.

---

## 4. Architectural Constraints

### 4.1 Governance Constraints (must hold for every request)

- No agent may serve traffic unless it has an active **Registry** record and an attached **Policy** bundle.
- All inbound prompts and outbound responses **must** transit **Model Armor** — there is no bypass path, even for internal callers.
- Tool and agent allow-lists are evaluated at **egress time** against the live Policy; cached decisions are not permitted across policy versions.

### 4.2 Identity & Trust Constraints

- Every Agent has a unique, non-reusable cryptographic **Agent Identity** issued at registration.
- Downstream tool/agent calls must carry the agent identity and the originating user identity (delegated trust, never impersonation).
- Sessions/Memory are partitioned by `tenant × user × agent`; cross-partition reads are forbidden.

### 4.3 Runtime Constraints

- The Model is treated as **stateless**; all continuity lives in Sessions/Memory.
- Memory writes are **append-only with TTL**; deletions occur via tombstones to preserve audit.
- A single agent invocation has bounded budgets: max tokens, max tool calls, max wall-clock time, max recursion depth for A2A.

### 4.4 Gateway Constraints

- Surfaces **must not** call the runtime directly — only through the Ingress Gateway.
- The runtime **must not** call external systems directly — only through the Egress Gateway.
- All MCP tool schemas are version-pinned per agent; schema drift triggers a policy re-evaluation.
- A2A calls are subject to loop detection and depth limits to prevent agent storms.

### 4.5 Observability Constraints

- Every component emits telemetry through the **Unified Telemetry Router** — no side-channel logging.
- Trace IDs propagate across surfaces, gateways, runtime, tools, and peer agents (W3C trace-context).
- PII in logs is redacted at the router, not at the consumer.
- Evaluation and Anomaly Detection are **read-only** consumers; they never mutate runtime state.

### 4.6 Non-Functional Constraints

- **Latency:** P95 ingress overhead ≤ 50 ms; P95 egress overhead ≤ 30 ms.
- **Availability:** Governance plane is on the critical path — must meet ≥ 99.95% SLO.
- **Isolation:** Tenants are logically isolated; noisy-neighbor protection at the Gateway.
- **Auditability:** Every decision (policy, armor, tool authz) produces an immutable audit record.

---

## 5. Design Principles

1. **Centralize policy, decentralize execution.** One place to author rules, many places to enforce them.
2. **Gateways are the only doors.** All ingress and egress is brokered, observable, and policy-checked.
3. **Identity is the unit of trust.** Agents, users, and tools all carry verifiable identity end-to-end.
4. **Memory is explicit.** State lives in Sessions/Memory — never hidden inside model calls.
5. **Observability is a first-class plane.** If it isn't traced, it didn't happen.
6. **Open protocols at the edges.** MCP for tools, A2A for agents — no proprietary lock-in at integration boundaries.

---

## 6. Component Interaction Matrix

| From → To | Surfaces | Ingress GW | Runtime | Egress GW | Tools | Other Agents | Telemetry |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| Surfaces | — | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Ingress GW | ✅ | — | ✅ | ❌ | ❌ | ❌ | ✅ |
| Runtime | ❌ | ✅ | — | ✅ | ❌ | ❌ | ✅ |
| Egress GW | ❌ | ❌ | ✅ | — | ✅ (MCP) | ✅ (A2A) | ✅ |
| Governance | ✅ | ✅ | ✅ | ✅ | — | — | ✅ |

✅ = allowed direct call · ❌ = forbidden (must route through a gateway)

---

## 7. Open Questions / Extension Points

- **Multi-region memory replication** — strong vs. eventual consistency for Sessions.
- **Policy simulation** — dry-run mode for proposed Policy changes against historical traffic.
- **Cost governance** — per-tenant token/tool budgets enforced at the Gateway.
- **Human-in-the-loop** — escalation hooks from Policy into approval workflows.
