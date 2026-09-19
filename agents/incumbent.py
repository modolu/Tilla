"""Frozen promoted champion adapter: the accepted Milestone 4 agent (commit 4fe8105).

Exposes ``agent(obs)`` from the self-contained snapshot package
``agents.incumbent_m4``. Never modified while evaluating a candidate; never
imports the mutable candidate runtime (``main``, ``kaggriculture_bot``).
"""

from agents.incumbent_m4.agent import agent

__all__ = ["agent"]
