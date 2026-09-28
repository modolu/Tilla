"""Frozen promoted champion adapter: the accepted Milestone 5 agent (commit 5c3f1ef).

Exposes ``agent(obs)`` from the self-contained snapshot package
``agents.incumbent_m5``. Never modified while evaluating a candidate; never
imports the mutable candidate runtime (``main``, ``kaggriculture_bot``). The
previous champion stays frozen in ``agents.incumbent_m4`` for diverse-mix
evaluation.
"""

from agents.incumbent_m5.agent import agent

__all__ = ["agent"]
