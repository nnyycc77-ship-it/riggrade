"""RigGrade benchmark container (spec sections 3, 4, 6 and 11).

The pure parts (identity parsing, statistics, bundle building, the session
client) run anywhere and are unit-tested. The GPU parts (gpu.py, rulers.py)
need a CUDA card with torch, vLLM and transformers, which the pinned image
provides; they are not exercised by the CPU test suite.
"""
