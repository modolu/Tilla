"""Frozen snapshot of the accepted Milestone 6 Tilla runtime (commit 21195e7).

Offline comparison code only: never packaged in the Kaggle submission and
never modified while a candidate is evaluated against it. Every module here
is a namespace-adjusted copy of the accepted ``kaggriculture_bot`` module of
the same name (imports point at ``agents.incumbent_m6``), plus ``agent.py``,
the copy of the accepted ``main.py``. It keeps its own episode memory, so
candidate-vs-incumbent self-play never shares state.
"""
