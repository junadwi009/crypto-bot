"""
governance/
Multi-layer safety governance modules.

Layer 0 — Hard Safety Kernel (this package, Phase 1 onwards)
Layer 3 — Meta Orchestrator        (Phase 2 onwards)

Modules in this package may be imported by application code, but only
governance-internal modules may MUTATE governance state. See safety_kernel
for the immutability contract.
"""
