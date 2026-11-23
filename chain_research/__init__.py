"""Chain research: web investigations guided by Claude.

From a topic, Claude prepares the questions; the chain (program + Claude Haiku as reader) answers each one with cited
proofs; the cycle repeats until Claude deems the knowledge solid; at the end, a complete PDF document.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
