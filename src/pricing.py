#!/usr/bin/env python3
"""
pricing.py — Python port of copilot-cost's TypeScript loader.ts/computeCost.

Parses pricing.snapshot.yaml and computes USD cost from ModelMetrics.
No external dependencies — stdlib only.

Pricing YAML search order:
  1. Path passed explicitly
  2. COPILOT_COST_PRICING env var
  3. ~/.copilot/cost-cache/pricing.yaml  (refreshed by npm run refresh-pricing)
  4. ~/tools/copilot-cost/pricing.snapshot.yaml
  5. scripts/pricing.snapshot.yaml  (bundled in this repo)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Pricing YAML candidate paths (in preference order)
# ---------------------------------------------------------------------------

_REPO_SNAPSHOT = Path(__file__).parent / "pricing.snapshot.yaml"
_COPILOT_HOME = Path(os.environ.get("COPILOT_DATA_HOME", Path.home() / ".copilot"))

_CANDIDATES: list[Path] = [
    _COPILOT_HOME / "cost-cache" / "pricing.yaml",
    Path.home() / "tools" / "copilot-cost" / "pricing.snapshot.yaml",
    _REPO_SNAPSHOT,
]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ModelPrice:
    vendor: str
    input: float        # USD per 1M tokens (fresh input)
    cached_input: float # USD per 1M tokens (cache read)
    output: float       # USD per 1M tokens (output)
    cache_write: Optional[float] = None  # Anthropic only; defaults to input if absent


# ---------------------------------------------------------------------------
# Model name normalizer — mirrors TypeScript normalizeModel()
# ---------------------------------------------------------------------------

def normalize_model(model_id: Optional[str]) -> Optional[str]:
    """Return a canonical model key matching pricing YAML entries, or None."""
    if not model_id:
        return None
    model = str(model_id).strip()
    # Strip parenthetical from "auto ...(actual-model)"
    m = re.match(r"^auto\b.*\(([^)]+)\)", model, re.IGNORECASE)
    if m:
        model = m.group(1)
    # Remove citation-style footnote annotations like [^1]
    model = re.sub(r"\[\^[^\]]+\]", "", model).strip()
    model = model.lower()
    model = re.sub(r"[\s_]+", "-", model)
    model = re.sub(r"[^a-z0-9.+\-]+", "-", model)
    model = re.sub(r"-+", "-", model)
    model = model.strip("-")
    # Strip known internal suffixes
    for suffix in ["-1m-internal", "-fast"]:
        if model.endswith(suffix):
            model = model[: -len(suffix)]
    return model or None


# ---------------------------------------------------------------------------
# YAML parser — handles pricing.snapshot.yaml format without PyYAML
# ---------------------------------------------------------------------------

def _strip_comment(line: str) -> str:
    """Remove inline # comments (all values in pricing YAML are scalars)."""
    pos = line.find("#")
    return line[:pos] if pos >= 0 else line


def _to_float(value: str) -> float:
    try:
        return float(value.strip())
    except (ValueError, TypeError):
        return 0.0


def _parse_yaml(path: Path) -> dict[str, ModelPrice]:
    """Parse pricing.snapshot.yaml into a dict keyed by model name."""
    prices: dict[str, ModelPrice] = {}
    current_model: Optional[str] = None
    current_fields: dict[str, str] = {}
    in_models = False

    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            line = _strip_comment(raw).rstrip()
            stripped = line.strip()
            if not stripped:
                continue
            indent = len(line) - len(line.lstrip())

            if indent == 0:
                if stripped == "models:":
                    in_models = True
                    if current_model and current_fields:
                        prices[current_model] = _make_price(current_fields)
                    current_model = None
                    current_fields = {}
                else:
                    in_models = False
                continue

            if not in_models:
                continue

            if indent == 2 and stripped.endswith(":") and ":" not in stripped[:-1]:
                if current_model and current_fields:
                    prices[current_model] = _make_price(current_fields)
                current_model = stripped[:-1]
                current_fields = {}
                continue

            if indent >= 4 and current_model and ":" in stripped:
                k, _, v = stripped.partition(":")
                current_fields[k.strip()] = v.strip()

    if current_model and current_fields:
        prices[current_model] = _make_price(current_fields)

    return prices


def _make_price(fields: dict[str, str]) -> ModelPrice:
    cw_raw = fields.get("cache_write")
    return ModelPrice(
        vendor=fields.get("vendor", ""),
        input=_to_float(fields.get("input", "0")),
        cached_input=_to_float(fields.get("cached_input", "0")),
        output=_to_float(fields.get("output", "0")),
        cache_write=_to_float(cw_raw) if cw_raw else None,
    )


# ---------------------------------------------------------------------------
# Loader (with simple in-process cache keyed on mtime+size)
# ---------------------------------------------------------------------------

_cache: dict[str, tuple[float, int, dict[str, ModelPrice]]] = {}


def load_pricing(path: Optional[str] = None) -> dict[str, ModelPrice]:
    """Load pricing YAML. Returns empty dict if no file is found."""
    env_path = os.environ.get("COPILOT_COST_PRICING")
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    if env_path:
        candidates.append(Path(env_path))
    candidates.extend(_CANDIDATES)

    for candidate in candidates:
        if not candidate.exists():
            continue
        resolved = str(candidate.resolve())
        try:
            st = candidate.stat()
        except OSError:
            continue
        cached = _cache.get(resolved)
        if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
            return cached[2]
        prices = _parse_yaml(candidate)
        _cache[resolved] = (st.st_mtime, st.st_size, prices)
        return prices

    return {}


# ---------------------------------------------------------------------------
# Cost computation — mirrors TypeScript computeCost()
# ---------------------------------------------------------------------------

def compute_cost(
    input_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    output_tokens: int,
    price: ModelPrice,
) -> float:
    """
    Compute USD cost from raw token counts.

    input_tokens includes cache_read and cache_write for Anthropic models.
    fresh = max(input_tokens - cache_read_tokens - cache_write_tokens, 0)
    """
    fresh = max(int(input_tokens) - int(cache_read_tokens) - int(cache_write_tokens), 0)
    cw_price = price.cache_write if price.cache_write is not None else price.input
    return (
        (fresh / 1_000_000) * price.input
        + (int(cache_read_tokens) / 1_000_000) * price.cached_input
        + (int(cache_write_tokens) / 1_000_000) * cw_price
        + (int(output_tokens) / 1_000_000) * price.output
    )


def estimate_session_cost(
    model_metrics: dict,
    pricing: Optional[dict[str, ModelPrice]] = None,
) -> Optional[float]:
    """
    Return total estimated USD cost for a session, or None if no pricing data
    matches any model in the session.

    model_metrics values may be ModelMetrics dataclass instances or plain dicts.
    """
    if pricing is None:
        pricing = load_pricing()
    if not pricing:
        return None

    total = 0.0
    matched = False
    for model_name, metrics in model_metrics.items():
        norm = normalize_model(model_name)
        if not norm:
            continue
        price = pricing.get(norm)
        if price is None:
            continue
        matched = True
        # Support both dataclass and dict representations
        if hasattr(metrics, "input_tokens"):
            total += compute_cost(
                metrics.input_tokens,
                metrics.cache_read_tokens,
                metrics.cache_write_tokens,
                metrics.output_tokens,
                price,
            )
        else:
            total += compute_cost(
                metrics.get("input_tokens", 0),
                metrics.get("cache_read_tokens", 0),
                metrics.get("cache_write_tokens", 0),
                metrics.get("output_tokens", 0),
                price,
            )

    return round(total, 6) if matched else None
