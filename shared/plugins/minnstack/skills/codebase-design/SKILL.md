---
name: codebase-design
description: Vocabulary and rules for designing deep modules. Use to improve an interface, choose a seam, deepen a design, make code testable or AI-navigable, or supply shared design terms.
metadata:
  opencode/slash: true
---

# Codebase design

Design **deep modules**: much behaviour behind a small interface, at a clean seam, tested through that interface.

## Vocabulary

- **Module**: anything with an interface and implementation. Avoid component, service, and unit.
- **Interface**: every fact a caller needs, including invariants, ordering, errors, configuration, and performance. Avoid API and signature.
- **Implementation**: code inside a module.
- **Seam**: the location where behaviour can change without editing the caller. Avoid boundary.
- **Adapter**: a concrete implementation at a seam.
- **Depth**: leverage per unit of interface learned. It is a property of the interface, not implementation size.
- **Leverage**: capability callers gain from depth.
- **Locality**: change, knowledge, and verification concentrated in one place.

## Shape the seam

Choose the seam before its implementation. Minimise methods, parameters, ordering rules, configuration, and exposed failures. Prefer one rich operation over coupled caller steps. Hide complexity that callers do not need.

Use the deletion test: deleting a deep module spreads its complexity across callers; deleting a pass-through removes little. Callers and tests cross the same interface. Internal seams may support implementation tests, but do not leak them into the external interface.

Introduce an adapter only when variation is real. One adapter is hypothetical indirection; two adapters justify a seam. Keep policy in the deep module and transport or infrastructure in adapters. Translate adapter errors at the seam.

Make dependencies inputs rather than construction details. Return useful values rather than relying only on mutation and side effects. These choices make behaviour testable through the interface.

## Refactor rules

- Replace the existing mechanism; do not add a parallel one. Extract today's behaviour as the first implementation and delete superseded dependencies.
- Move a symbol by updating every call site. Leave compatibility shims only for genuine external consumers.
- Give an extension point a default behaviour. Do not make callers branch on sentinels or opt-in flags.
- Widen a suitable union before adding a near-duplicate component.
- A decorator must delegate after its work. Use a callback when delegation is not part of the role.
- Do not expose one subsystem's model as another subsystem's convenience property. Construct it at the call site.
- Do not create a module for trivial one-off code. Extract only for reuse or meaningful behaviour.

Use [DEEPENING.md](DEEPENING.md) to classify dependencies and replace shallow tests. When materially different interfaces remain plausible, use [DESIGN-IT-TWICE.md](DESIGN-IT-TWICE.md).
