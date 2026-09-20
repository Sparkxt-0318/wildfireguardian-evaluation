#!/usr/bin/env bash
# Regenerate every committed artefact, in the order the audit requires.
#
#     bash experiments/regenerate_all.sh
#
# Fails on the first step that fails, so a broken artefact cannot be committed
# silently.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== fixtures =="
python tests/fixtures/generate_fixtures.py

echo
echo "== mutation audit =="
python experiments/run_mutation_audit.py --out reports

echo
echo "== demonstration =="
python experiments/run_demonstration.py --out experiments/output

echo
echo "== wording audit over every generated artefact =="
python - <<'PY'
import sys
from pathlib import Path
from wg_eval.report import audit_wording

paths = [
    "experiments/output/DEMONSTRATION.md",
    "experiments/output/report.md",
    "experiments/output/redteam.md",
    "reports/MUTATION_TEST_AUDIT.md",
]
failed = False
for path in paths:
    findings = audit_wording(Path(path).read_text(encoding="utf-8"))
    print(f"{path:44s} {len(findings)} findings")
    for finding in findings:
        failed = True
        print(f"    {finding['phrase']!r}: {finding['guidance']}")
        print(f"      ...{finding['context']}...")
sys.exit(1 if failed else 0)
PY

echo
echo "all artefacts regenerated and clean"
