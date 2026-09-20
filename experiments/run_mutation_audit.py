#!/usr/bin/env python3
"""Run the mutation audit and write ``reports/MUTATION_TEST_AUDIT.md``.

    python experiments/run_mutation_audit.py --out reports
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from wg_eval.mutation import run_mutation_audit, summary
from wg_eval.version import code_version

PREAMBLE = """\
A test suite that passes tells you nothing until you know it can fail. This audit
introduces deliberate statistical errors -- the exact errors the library exists
to prevent -- and records whether anything catches each one.

Random operator mutations are not used. They mostly produce crashes, and a
crash is not the failure mode that matters here. The interesting mutants are
the ones that produce *plausible numbers that are wrong*, so each is written by
hand, together with the check a reader would expect to notice it.

A mutant that nothing detects is reported as an undetected mutant, not omitted.
"""

CLOSING = """\
## What this audit does not establish

Detecting a mutant shows that *some* check distinguishes the broken library
from the correct one. It does not show that the check would fire on a
different dataset, that the message would be understood, or that a reader would
act on it. Those are properties of the report and of the person reading it.

Two mutants needed their fixtures designed with care, and the reasons are worth
recording because they are facts about the statistics rather than about the code:

* **`observation_bootstrap`.** The narrowing that pseudoreplication buys is
  bounded. With `sigma_I` the unit-by-policy interaction, `V_E` the variance of
  a paired sub-unit difference and `E` sub-units per unit, the variance ratio is
  `(2*sigma_I^2 + V_E) / (2*E*sigma_I^2 + V_E)`. It tends to `1/E` when the
  interaction dominates and to **1** when there is none: on a design with no
  unit-by-policy interaction the two bootstraps estimate the same quantity, and
  no fixture can detect this mutant. "Resampling observations always understates
  uncertainty" is therefore false as stated; what is true is that it understates
  it whenever the units differ in how they respond to the policies.

* **A naive bootstrap that also drops the pairing makes two errors at once.**
  Ignoring clustering narrows the interval; ignoring pairing widens it. On a
  strongly paired design the second dominates, and the doubly-wrong interval
  looks conservative. The two errors are therefore separate mutants here, and
  the pseudoreplication mutant holds the pairing fixed.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="reports")
    args = parser.parse_args()

    results = run_mutation_audit()
    report = summary(results)

    lines = [
        "# Mutation test audit",
        "",
        f"{code_version()}",
        "",
        PREAMBLE,
        "",
        f"**{report['n_detected']} of {report['n_mutants']} mutants detected.**",
        "",
    ]
    if report["undetected"]:
        lines += [
            "**Undetected mutants (holes in the suite):** "
            + ", ".join(f"`{k}`" for k in report["undetected"]),
            "",
        ]
    else:
        lines += ["No undetected mutants.", ""]

    lines += ["## Mutants", ""]
    header = ["mutant", "what it breaks", "wrong behaviour", "audit item", "detected"]
    widths = [len(h) for h in header]
    rows = []
    for r in results:
        row = [
            f"`{r.mutant.key}`", r.mutant.description, r.mutant.wrong_behaviour,
            r.mutant.audit_item, "yes" if r.detected else "**NO**",
        ]
        rows.append(row)
        widths = [max(w, len(c)) for w, c in zip(widths, row)]
    lines.append("| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(header)) + " |")
    lines.append("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    for row in rows:
        lines.append("| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(row)) + " |")
    lines.append("")

    lines += ["## Detection detail", ""]
    for r in results:
        verdict = "detected" if r.detected else "**UNDETECTED**"
        lines.append(f"- **`{r.mutant.key}`** — {verdict}."
                     + (f" {r.detail}" if r.detail else ""))
    lines += ["", CLOSING]

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "MUTATION_TEST_AUDIT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (outdir / "mutation_audit.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(f"wrote {outdir / 'MUTATION_TEST_AUDIT.md'}")
    print(f"wrote {outdir / 'mutation_audit.json'}")
    print(f"{report['n_detected']}/{report['n_mutants']} mutants detected")
    return 0 if not report["undetected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
