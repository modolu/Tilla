"""Frozen promoted champion adapter: the accepted Milestone 7 agent (commit 6ed9030).

Exposes ``agent(obs)`` from the self-contained snapshot package
``agents.incumbent_m7``. Never modified while evaluating a candidate; never
imports the mutable candidate runtime (``main``, ``kaggriculture_bot``). The
previous champions stay frozen in ``agents.incumbent_m6``, ``agents.incumbent_m5``
and ``agents.incumbent_m4`` for diverse-mix evaluation.
"""

from agents.incumbent_m7.agent import agent

__all__ = ["agent"]
