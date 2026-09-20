"""Multiplicity correction over a *declared* family of hypotheses.

A correction is only meaningful relative to a family, and the family depends on
the decision -- which metrics, which policies, which strata were candidates for
the claim.  The library cannot infer that, so it never corrects automatically.
Instead each metric carries an analysis role, the roles define the families, and
the configuration names the correction that applies to the secondary family.

What is corrected, and what is not:

* **p-values** are adjusted.  ``holm`` is a step-down procedure controlling the
  family-wise error rate under arbitrary dependence; ``bonferroni`` is the
  single-step version of the same guarantee.
* **Confidence intervals are not adjusted.**  A Holm-adjusted interval is not a
  simple rescaling of an unadjusted one, and silently widening intervals would
  misrepresent what was computed.  Reports therefore show marginal intervals
  next to adjusted p-values, and say so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

METHODS = ("none", "holm", "bonferroni")


@dataclass(frozen=True)
class AdjustedTest:
    """One hypothesis inside a declared family."""

    key: str
    role: str
    p_raw: float | None
    p_adjusted: float | None
    method: str
    family_size: int
    alpha: float

    @property
    def survives(self) -> bool | None:
        """Whether the finding is retained at ``alpha`` after adjustment."""
        if self.p_adjusted is None:
            return None
        return bool(self.p_adjusted <= self.alpha)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "role": self.role,
            "p_raw": self.p_raw,
            "p_adjusted": self.p_adjusted,
            "method": self.method,
            "family_size": self.family_size,
            "alpha": self.alpha,
            "survives": self.survives,
        }


@dataclass
class FamilyResult:
    """The outcome of applying one correction to one declared family."""

    role: str
    method: str
    alpha: float
    tests: list[AdjustedTest] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def family_size(self) -> int:
        return len(self.tests)

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "method": self.method,
            "alpha": self.alpha,
            "family_size": self.family_size,
            "tests": [t.as_dict() for t in self.tests],
            "notes": list(self.notes),
        }


def holm(pvalues: Mapping[str, float]) -> dict[str, float]:
    """Holm-Bonferroni step-down adjusted p-values.

    For sorted raw values ``p_(1) <= ... <= p_(m)`` the adjusted value at rank
    ``i`` is ``max_{j<=i} min(1, (m - j + 1) * p_(j))``; the running maximum
    enforces monotonicity so an adjusted p-value never falls below one ranked
    ahead of it.
    """
    items = [(k, float(v)) for k, v in pvalues.items()]
    if not items:
        return {}
    m = len(items)
    ordered = sorted(items, key=lambda kv: kv[1])
    out: dict[str, float] = {}
    running = 0.0
    for rank, (key, p) in enumerate(ordered):
        adjusted = min(1.0, (m - rank) * p)
        running = max(running, adjusted)
        out[key] = running
    return out


def bonferroni(pvalues: Mapping[str, float]) -> dict[str, float]:
    """Single-step Bonferroni adjusted p-values: ``min(1, m * p)``."""
    m = len(pvalues)
    return {k: min(1.0, m * float(v)) for k, v in pvalues.items()}


def adjust(pvalues: Mapping[str, float], method: str) -> dict[str, float]:
    """Apply ``method`` to a family of raw p-values."""
    if method not in METHODS:
        raise ValueError(f"unknown multiplicity method {method!r}; supported: {METHODS}")
    if method == "none":
        return {k: float(v) for k, v in pvalues.items()}
    if method == "holm":
        return holm(pvalues)
    return bonferroni(pvalues)


def apply_to_family(
    *,
    role: str,
    method: str,
    alpha: float,
    pvalues: Mapping[str, float | None],
) -> FamilyResult:
    """Adjust one role's family, keeping tests with no p-value visible."""
    usable = {k: float(v) for k, v in pvalues.items() if v is not None}
    adjusted = adjust(usable, method)
    result = FamilyResult(role=role, method=method, alpha=alpha)
    for key, raw in pvalues.items():
        result.tests.append(
            AdjustedTest(
                key=key,
                role=role,
                p_raw=None if raw is None else float(raw),
                p_adjusted=adjusted.get(key),
                method=method,
                family_size=len(usable),
                alpha=alpha,
            )
        )
    if method == "none":
        result.notes.append(
            f"The {role} family of {len(usable)} test(s) is reported without multiplicity "
            "correction, as declared. Read it as that many separate comparisons."
        )
    else:
        result.notes.append(
            f"{method.capitalize()} correction applied across the declared {role} family of "
            f"{len(usable)} test(s) at alpha={alpha:g}. Confidence intervals shown elsewhere "
            "are marginal and are NOT adjusted for multiplicity."
        )
    if role == "primary" and len(usable) > 1:
        result.notes.append(
            f"{len(usable)} tests carry the primary role. A primary family larger than one "
            "hypothesis needs a correction of its own or a declared ordering."
        )
    return result


def summarize(families: list[FamilyResult]) -> dict[str, Any]:
    """A compact description of every declared family, for reports."""
    return {
        "families": [f.as_dict() for f in families],
        "total_tests": sum(f.family_size for f in families),
        "corrected": sorted({f.method for f in families if f.method != "none"}),
    }
