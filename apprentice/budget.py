"""Spend ledger and hard budget guard for paid model calls.

Every real request is priced *before* it is sent (worst case: estimated input plus the full
output allowance) and refused if it could push the total past the budget. After the call the
real usage is written to an append-only JSONL ledger, which is the source of truth.

The budget comes from APPRENTICE_BUDGET_USD and defaults to 0, so nothing is ever spent until
someone sets it on purpose.
"""

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

# USD per 1M tokens: (input, cached input, output). Source: dev.meta.ai/docs/pricing-rate-limits.
PRICES = {
    "muse-wonder-1.0": (4.00, 0.20, 20.00),
    "muse-spark-1.3": (1.25, 0.15, 4.25),
    "muse-spark-1.3-contributor": (0.10, 0.002, 0.20),
}

# Guess for one 1280x800 screenshot, used only for the pre-call estimate; recalibrate from the
# ledger after the first real calls.
DEFAULT_IMAGE_TOKENS = 2500


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Usage:
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0

    @classmethod
    def from_response(cls, usage):
        usage = usage or {}
        details = usage.get("input_tokens_details") or {}
        return cls(usage.get("input_tokens", 0), details.get("cached_tokens", 0),
                   usage.get("output_tokens", 0))

    def cost(self, model):
        p_in, p_cached, p_out = price(model)
        fresh = max(self.input_tokens - self.cached_tokens, 0)
        return (fresh * p_in + self.cached_tokens * p_cached + self.output_tokens * p_out) / 1e6


def price(model):
    if model not in PRICES:
        raise BudgetExceeded(f"no price known for {model!r}; add it to PRICES before calling it")
    return PRICES[model]


class Ledger:
    def __init__(self, path, budget_usd=None, margin_usd=1.0):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if budget_usd is None:
            budget_usd = float(os.environ.get("APPRENTICE_BUDGET_USD", "0"))
        self.budget = budget_usd
        self.margin = margin_usd
        self._lock = threading.Lock()

    def entries(self):
        if not self.path.exists():
            return []
        with self.path.open() as f:
            return [json.loads(line) for line in f if line.strip()]

    def spent(self):
        return sum(e["cost_usd"] for e in self.entries() if not e.get("mock"))

    def remaining(self):
        return self.budget - self.margin - self.spent()

    def check(self, model, est_input_tokens, max_output_tokens):
        """Raise unless the worst case of this call still fits under the budget."""
        p_in, _, p_out = price(model)
        worst = (est_input_tokens * p_in + max_output_tokens * p_out) / 1e6
        left = self.remaining()
        if worst > left:
            raise BudgetExceeded(
                f"refusing {model} call: worst case ${worst:.4f} > ${left:.4f} left "
                f"(budget ${self.budget:.2f}, margin ${self.margin:.2f}, spent ${self.spent():.4f})")
        return worst

    def record(self, model, usage, mock=False, **extra):
        entry = {"ts": time.time(), "model": model, "mock": mock,
                 "input_tokens": usage.input_tokens, "cached_tokens": usage.cached_tokens,
                 "output_tokens": usage.output_tokens, "cost_usd": usage.cost(model), **extra}
        with self._lock, self.path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        return entry
