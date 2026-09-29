"""Frozen promoted champion adapter: the accepted Milestone 6 agent (commit 21195e7).

Exposes ``agent(obs)`` from the self-contained snapshot package
``agents.incumbent_m6``. Never modified while evaluating a candidate; never
imports the mutable candidate runtime (``main``, ``kaggriculture_bot``). The
previous champions stay frozen in ``agents.incumbent_m5`` and
``agents.incumbent_m4`` for diverse-mix evaluation.
"""

from agents.incumbent_m6.agent import agent

__all__ = ["agent"]
