"""The statistical core must not know what domain its data came from.

This is the property that makes the library usable as an evaluator: it cannot
be tuned, even unconsciously, towards the conclusion the producing system would
prefer.  The check is mechanical -- a vocabulary scan over the source -- so it
cannot drift the way a convention does.

Four schema column names (``resident_id``, ``mission_success``,
``responder_exposure``, ``travel_time``) come from the interface contract the
library was specified against and are treated as opaque identifiers everywhere
in the code. They are listed here explicitly rather than quietly excluded.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "wg_eval"

#: Vocabulary that would mean the statistical core had learned a domain.
FORBIDDEN = [
    r"wildfire",
    # "fire" as an English verb is fine ("the trap does not fire"); the domain
    # usage is the noun in a compound, so the patterns target the compounds.
    r"\bforest fires?\b",
    r"\bfire (spread|regime|perimeter|season|behaviou?r|danger|risk|weather|line|crew)\b",
    r"\bsmoke\b",
    r"\bburn",
    r"evacuat",
    r"vulnerab",
    r"guardian",
    r"\bflame",
    r"\bignit",
    r"\bterrain\b",
    r"\bweather\b",
    r"\bacres?\b",
    r"\bhectares?\b",
    r"\bfirefight",
    r"\bincident\b",
]

#: Interface-contract column names. Opaque strings, not domain knowledge.
CONTRACT_NAMES = {"resident_id", "mission_success", "responder_exposure", "travel_time"}


def source_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_the_scan_covers_every_module():
    names = {p.name for p in source_files()}
    assert {"compare.py", "bootstrap.py", "metrics.py", "hierarchy.py"} <= names
    assert len(names) >= 15


@pytest.mark.parametrize("path", source_files(), ids=lambda p: p.name)
def test_no_domain_vocabulary_in_the_source(path: Path):
    text = path.read_text(encoding="utf-8")
    offenders = []
    for pattern in FORBIDDEN:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            line = text.count("\n", 0, match.start()) + 1
            context = text.splitlines()[line - 1].strip()
            offenders.append(f"{path.name}:{line}: {match.group(0)!r} in {context!r}")
    assert not offenders, "domain vocabulary in the statistical core:\n" + "\n".join(offenders)


def test_contract_column_names_are_used_as_opaque_identifiers():
    """They may name a column; they may not carry meaning in logic or defaults."""
    from wg_eval.schema import SCHEMA_BY_NAME

    for name in CONTRACT_NAMES:
        spec = SCHEMA_BY_NAME[name]
        # The description explains the column's STRUCTURAL role, not its subject.
        assert not re.search(r"wildfire|evacuat|fire\b", spec.description, re.IGNORECASE)


def test_synthetic_strata_are_design_variables_not_domain_categories():
    from wg_eval.synth.generators import DEFAULT_STRATA

    assert set(DEFAULT_STRATA) == {"difficulty", "scale", "regime", "capacity"}
    for levels in DEFAULT_STRATA.values():
        for level in levels:
            assert not re.search(r"|".join(FORBIDDEN), level, re.IGNORECASE)


def test_failure_reasons_and_actions_are_generic():
    from wg_eval.synth.generators import ACTIONS, DEFAULT_FAILURE_MIX

    for label in list(ACTIONS) + list(DEFAULT_FAILURE_MIX):
        assert re.fullmatch(r"(action_\d+|reason_[a-z])", label), label


def test_the_library_never_hard_codes_a_domain_threshold():
    """Every numeric default is a statistical constant, not a domain quantity."""
    from wg_eval.config import AnalysisConfig, BootstrapSpec, MissingDataSpec

    boot = BootstrapSpec()
    assert (boot.n_resamples, boot.confidence_level, boot.method) == (2000, 0.95, "percentile")
    assert MissingDataSpec().policy == "drop_record"
    # No default margin exists anywhere: a margin is a domain judgement.
    assert AnalysisConfig.__dataclass_fields__["equivalence"].default_factory().margins == {}
