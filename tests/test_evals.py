"""Pytest wrapper so `pytest` also runs the offline eval suite."""

from evals.cases import CASES, RUNNERS


def test_offline_evals():
    failures = []
    for spec in CASES:
        runner = RUNNERS[spec.case_id]
        result = runner(live=False) if spec.case_id == "e2e_2026_07_22_template" else runner()
        if not result.passed:
            failed = [c for c in result.checks if not c.passed]
            failures.append(
                f"{result.case_id}: " + "; ".join(f"{c.name} ({c.detail})" for c in failed)
            )
    assert not failures, "Eval failures:\n" + "\n".join(failures)
