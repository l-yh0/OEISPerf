import argparse
import json
import math
import os
import sys
from dataclasses import asdict
from typing import Any, Dict, List

from tqdm import tqdm

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from utils import (
    evalPrompt,
    evaluate_sequential,
    extract_code_from_response,
    normalize_message_content,
    sanitize_model_name,
    write_output_file,
    require_prlimit,
    OEISPerfLoader,
    ResultsWithVerdict,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate and evaluate candidate code.")
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-5.2-2025-12-11",
        help="Model name used by ChatOpenAI.",
    )
    parser.add_argument(
        "--apikey",
        type=str,
        default=None,
        help="OpenAI API key. If omitted, OPENAI_API_KEY is used.",
    )
    parser.add_argument(
        "--baseurl",
        type=str,
        default=None,
        help="OpenAI-compatible base URL. If omitted, use OPENAI_BASE_URL or OPENAI_API_BASE.",
    )
    parser.add_argument(
        "--maxtokens",
        type=int,
        default=1024 * 32,
        help="Maximum output tokens for each model response.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=1.0,
        help="Sampling temperature (the paper uses 1.0).",
    )
    parser.add_argument(
        "--extra-body",
        type=json.loads,
        default=None,
        help=(
            "JSON object added to the request body, for provider-specific options such as the "
            "thinking mode, e.g. '{\"enable_thinking\": true}'."
        ),
    )
    parser.add_argument(
        "--split",
        type=str,
        choices=OEISPerfLoader.SPLITS,
        default="test",
        help="Dataset split to evaluate (the paper reports the test split).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only use the first N instances (quick smoke test; not comparable with the paper).",
    )
    args = parser.parse_args()
    if args.extra_body is not None and not isinstance(args.extra_body, dict):
        parser.error("--extra-body must be a JSON object.")
    return args


def check_environment() -> None:
    """Fail fast, before any LLM call, if the evaluation sandbox cannot work."""
    try:
        import sympy  # noqa: F401
    except ImportError as exc:
        raise EnvironmentError(
            "SymPy is required: candidate programs import it inside the evaluation sandbox "
            "(pip install -r requirements.txt)."
        ) from exc
    require_prlimit()


def compute_metrics(results: List[ResultsWithVerdict], model_name: str) -> Dict[str, float]:
    """Compute summary metrics for the generated programs.

    Definitions follow Section 2.4 of the paper:
    - Reference attainment rate: share of candidates that are correct and no slower than the reference.
    - Reference-normalized speedup score: clipped, log-scaled speedup averaged over tasks
      (an incorrect candidate contributes zero).
    - No valid gain rate: share of candidates that fail validation or are slower than the inefficient baseline.
    """

    total = len(results)
    if total == 0:
        return {
            "reference_attainment_rate": 0.0,
            "reference_normalized_speedup_score": 0.0,
            "no_valid_gain_rate": 0.0,
        }

    attainment_hits = 0
    speedup_sum = 0.0
    no_gain_count = 0

    for res in results:
        cand_valid = bool(res.respVerdict.get(model_name, False))
        cand_rt = res.respRuntime.get(model_name)
        ineff_rt = res.inefficientRuntime
        ref_rt = res.referenceRuntime

        # Both baselines must be timed and the reference must be faster, otherwise the environment is
        # broken (missing prlimit/SymPy, timeouts, ...) and no metric is meaningful.
        if ineff_rt is None or ref_rt is None:
            raise RuntimeError(
                f"Instance {res.index}: inefficient/reference runtime unavailable "
                "(baseline failed validation or timed out); metrics are undefined.")
        if not ref_rt < ineff_rt:
            raise RuntimeError(
                f"Instance {res.index}: reference ({ref_rt:.0f} ns) is not faster than the "
                f"inefficient program ({ineff_rt:.0f} ns); metrics are undefined.")

        if cand_valid and cand_rt is not None:
            # Reference attainment: candidate runtime no greater than the reference's.
            if cand_rt <= ref_rt:
                attainment_hits += 1

            # Normalized speedup contribution (clip to [0,1]).
            ratio = (math.log(ineff_rt) - math.log(cand_rt)) / \
                (math.log(ineff_rt) - math.log(ref_rt))
            speedup_sum += max(0.0, min(1.0, ratio))

        # No valid gain: invalid candidates or slower than the inefficient baseline.
        if not cand_valid or cand_rt is None or ineff_rt < cand_rt:
            no_gain_count += 1

    return {
        "reference_attainment_rate": attainment_hits / total,
        "reference_normalized_speedup_score": (speedup_sum / total) * 100.0,
        "no_valid_gain_rate": no_gain_count / total,
    }


def main() -> None:
    args = parse_args()
    check_environment()

    model_name = args.model
    split_suffix = "" if args.split == "test" else f".{args.split}"
    output_path = f"results/{sanitize_model_name(model_name)}{split_suffix}.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    resolved_api_key = args.apikey or os.getenv("OPENAI_API_KEY")
    if not resolved_api_key:
        raise EnvironmentError("OPENAI_API_KEY is not set in the environment.")
    resolved_base_url = args.baseurl or os.getenv(
        "OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")

    # References are decrypted in memory only and never written to disk (see the serialization below).
    records = OEISPerfLoader.load(args.split).to_dict(orient="records")
    if args.limit is not None:
        records = records[: args.limit]
    print(
        f"[INFO] Loaded {len(records)} instances ({args.split} split).", file=sys.stderr)

    llm_kwargs: Dict[str, Any] = {
        "model": model_name,
        "max_tokens": args.maxtokens,
        "temperature": args.temperature,
        "api_key": resolved_api_key,
    }
    if resolved_base_url:
        llm_kwargs["base_url"] = resolved_base_url
    if args.extra_body:
        llm_kwargs["extra_body"] = args.extra_body

    llm = ChatOpenAI(**llm_kwargs)

    result_code: Dict[str, Dict[str, str]] = {model_name: {}}
    raw_responses: Dict[str, Dict[str, str]] = {model_name: {}}

    write_output_file(
        output_path,
        model_name,
        args.maxtokens,
        result_code,
        evaluation_results=[],
        raw_responses=raw_responses,
    )

    for d in tqdm(records, desc="Generating", unit="item"):
        index = str(d["id"])
        prompt = evalPrompt.format(CODE=d["inefficientCode"])

        raw_response = ""
        try:
            response = llm.invoke([HumanMessage(content=prompt)])
            raw_response = normalize_message_content(
                getattr(response, "content", ""))
        except Exception as exc:
            print(
                f"[ERROR] LLM call failed for index {index}: {exc}", file=sys.stderr)

        raw_responses[model_name][index] = raw_response
        result_code[model_name][index] = extract_code_from_response(
            raw_response)

        # Write checkpoint after each generated sample.
        write_output_file(
            output_path,
            model_name,
            args.maxtokens,
            result_code,
            evaluation_results=[],
            raw_responses=raw_responses,
        )

    print("[INFO] Starting sequential runtime evaluation...", file=sys.stderr)
    results = evaluate_sequential(
        records,
        result_code,
        model_name,
        runtime_test_nums=50,
        timeout=3400.0,
    )

    # Do not write the decrypted reference to the results file.
    serialized_results = [
        {k: v for k, v in asdict(result).items() if k != "referenceCode"}
        for result in results
    ]
    # Save before computing metrics: compute_metrics validates the baselines and may raise.
    write_output_file(
        output_path,
        model_name,
        args.maxtokens,
        result_code,
        serialized_results,
        raw_responses,
    )

    metrics = compute_metrics(results, model_name)
    write_output_file(
        output_path,
        model_name,
        args.maxtokens,
        result_code,
        serialized_results,
        raw_responses,
        metrics=metrics,
    )
    print(
        f"[INFO] Done. Saved final output to {output_path}.", file=sys.stderr)
    print(
        (
            f"[INFO] Metrics — reference attainment: {metrics['reference_attainment_rate']:.3f}, "
            f"reference-normalized speedup: {metrics['reference_normalized_speedup_score']:.2f}, "
            f"no valid gain: {metrics['no_valid_gain_rate']:.3f}"
        ),
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
