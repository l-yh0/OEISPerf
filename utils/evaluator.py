import sys
from typing import Any, Dict, List

from tqdm import tqdm

from .dataModel import ResultsWithVerdict
from .valWorker import validate_candidate_code


def evaluate_sequential(
    records: List[Dict[str, Any]],
    result_code: Dict[str, Dict[str, str]],
    model_name: str,
    *,
    runtime_test_nums: int = 50,
    timeout: float = 3400.0,
) -> List[ResultsWithVerdict]:
    """Evaluate inefficient, reference, and candidate code sequentially (no parallelism)."""

    results: List[ResultsWithVerdict] = []

    for record in tqdm(records, desc="Evaluating", unit="item"):
        index = str(record["id"])
        inefficient_code: str = record["inefficientCode"]
        reference_code: str = record["referenceCode"]
        expected_output_raw = record.get("expected_output")
        offset_start = int(record.get("offset_start", 0))

        if not expected_output_raw:
            print(
                f"[WARN] Skip {index}: missing expected_output.", file=sys.stderr)
            continue

        try:
            expected_output = [int(x) for x in expected_output_raw]
        except Exception as exc:
            print(
                f"[WARN] Skip {index}: expected_output parse error {exc}.", file=sys.stderr)
            continue

        result = ResultsWithVerdict(
            index=index,
            inefficientCode=inefficient_code,
            inefficientRuntime=None,
            referenceCode=reference_code,
            referenceRuntime=None,
            respCode={},
            respRuntime={},
            respVerdict={},
        )

        candidate_code = result_code.get(model_name, {}).get(index)
        if candidate_code:
            result.respCode[model_name] = candidate_code

        # Inefficient correctness + runtime (should pass)
        try:
            inefficient_res = validate_candidate_code(
                expected_output,
                offset_start,
                inefficient_code,
                runtime_test_nums=runtime_test_nums,
                timeout=timeout,
            )
            if inefficient_res.ok:
                result.inefficientRuntime = inefficient_res.elapsed_time
        except Exception as exc:
            print(
                f"[WARN] Inefficient validation failed for {index}: {exc}", file=sys.stderr)

        # Reference correctness + runtime (should pass)
        try:
            reference_res = validate_candidate_code(
                expected_output,
                offset_start,
                reference_code,
                runtime_test_nums=runtime_test_nums,
                timeout=timeout,
            )
            if reference_res.ok:
                result.referenceRuntime = reference_res.elapsed_time
        except Exception as exc:
            print(
                f"[WARN] Reference validation failed for {index}: {exc}", file=sys.stderr)

        # Candidate correctness + runtime
        if candidate_code:
            try:
                cand_res = validate_candidate_code(
                    expected_output,
                    offset_start,
                    candidate_code,
                    runtime_test_nums=runtime_test_nums,
                    timeout=timeout,
                )
                result.respVerdict[model_name] = cand_res.ok
                if cand_res.ok:
                    result.respRuntime[model_name] = cand_res.elapsed_time
            except Exception as exc:
                print(
                    f"[WARN] Candidate validation failed for {index}: {exc}", file=sys.stderr)
                result.respVerdict[model_name] = False
        else:
            result.respVerdict[model_name] = False

        results.append(result)

    return results
