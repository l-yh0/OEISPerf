from __future__ import annotations
from typing import List, Tuple
import platform

import json
import os
import sys
import re
import tempfile
import shutil
import subprocess
import traceback
from typing import List, Optional, Tuple, Dict, Any
import ast
import tokenize
import io
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any


from .dataModel import *


RUNTIME_HEADER = ""

# Keep the original driver (compatible/rollback), but the new batch correctness no longer forks per single n
SUBPROCESS_DRIVER = r"""
import sys
import json
import traceback
import time

def _oeis_runtime_main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "__probe__"
    calc = globals().get("calculator")

    if mode == "__probe__":
        if not callable(calc):
            print(json.dumps({
                "ok": False,
                "error": "No callable `calculator` found in executed namespace."
            }))
        else:
            print(json.dumps({"ok": True}))
        return

    if mode == "__eval__":
        if not callable(calc):
            print(json.dumps({
                "ok": False,
                "error": "No callable `calculator` found in executed namespace."
            }))
            return

        if len(sys.argv) < 3:
            print(json.dumps({
                "ok": False,
                "error": "Missing n argument for __eval__"
            }))
            return

        try:
            n = int(sys.argv[2])
        except Exception as e:
            print(json.dumps({
                "ok": False,
                "error": f"Invalid n argument: {e}"
            }))
            return

        try:
            t_start = time.process_time_ns()
            value = calc(n)
            t_cost = time.process_time_ns() - t_start
        except Exception as e:
            print(json.dumps({
                "ok": False,
                "error": f"Exception while running calculator({n}): {e}",
                "traceback": traceback.format_exc(),
            }))
            return

        if isinstance(value, bool):
            print(json.dumps({
                "ok": False,
                "kind": "bool",
                "value": bool(value),
            }))
            return

        try:
            ivalue = int(value)
        except Exception:
            print(json.dumps({
                "ok": False,
                "kind": "non-int",
                "repr": repr(value),
            }))
            return

        print(json.dumps({
            "ok": True,
            "kind": "int",
            "value": ivalue,
            "exec_time": t_cost
        }))
        return

    print(json.dumps({
        "ok": False,
        "error": f"Unknown mode {mode!r}",
    }))

if __name__ == "__main__":
    _oeis_runtime_main()
"""


# correctness: one subprocess receives all n via JSON; every n runs in a fresh sandbox (avoid cache hacks)
CORRECTNESS_BATCH_SCRIPT = r"""
import sys
import json
import traceback
import time

def _read_json_arg(s: str):
    # Optional: allow @/path/to/file.json to avoid long CLI args; otherwise parse JSON string directly
    if s.startswith("@"):
        path = s[1:]
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(s)

def correctness_main(code_path: str, ns_arg: str):
    try:
        ns = _read_json_arg(ns_arg)
        if not isinstance(ns, list) or len(ns) == 0:
            raise ValueError("ns must be a non-empty JSON list")

        ns = [int(x) for x in ns]

        with open(code_path, "r", encoding="utf-8") as f:
            code_str = f.read()

        code_obj = compile(code_str, "<candidate>", "exec")

        # probe: ensure the code executes and exposes a callable calculator
        sandbox_probe = {"__name__": "__main__"}
        exec(code_obj, sandbox_probe)
        func_probe = sandbox_probe.get("calculator")
        if not callable(func_probe):
            print(json.dumps({
                "ok": False,
                "error": "No callable `calculator` found in sandbox.",
            }))
            return

        results = []
        for n in ns:
            sandbox = {"__name__": "__main__"}  # fresh sandbox each time to avoid cache contamination
            try:
                exec(code_obj, sandbox)
                func = sandbox.get("calculator")
                if not callable(func):
                    results.append({
                        "n": n,
                        "ok": False,
                        "error": "No callable `calculator` found in sandbox.",
                    })
                    continue

                t0 = time.process_time_ns()
                value = func(n)
                t_cost = time.process_time_ns() - t0

                if isinstance(value, bool):
                    results.append({
                        "n": n,
                        "ok": False,
                        "kind": "bool",
                        "value": bool(value),
                        "exec_time": t_cost,
                    })
                    continue

                try:
                    ivalue = int(value)
                except Exception:
                    results.append({
                        "n": n,
                        "ok": False,
                        "kind": "non-int",
                        "repr": repr(value),
                        "exec_time": t_cost,
                    })
                    continue

                results.append({
                    "n": n,
                    "ok": True,
                    "kind": "int",
                    "value": ivalue,
                    "exec_time": t_cost,
                })

            except Exception as e:
                results.append({
                    "n": n,
                    "ok": False,
                    "error": f"Exception while running calculator({n}): {e}",
                    "traceback": traceback.format_exc(),
                })

        print(json.dumps({
            "ok": True,
            "results": results,
        }))

    except Exception as e:
        print(json.dumps({
            "ok": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }))

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(json.dumps({"ok": False, "error": "Args error: need code_path and ns_json"}))
        raise SystemExit(2)
    correctness_main(sys.argv[1], sys.argv[2])
"""


# benchmark: keep a fresh sandbox for every func(n) call to avoid cache artifacts (core policy unchanged)
# Improvement: read code from code_path instead of embedding in triple quotes (prevents ''' from breaking the template)
BENCH_SCRIPT_TEMPLATE = r"""
import time
import json
import sys
import gc
import statistics

def _read_json_arg(s: str):
    if s.startswith("@"):
        path = s[1:]
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(s)

def benchmark_main(code_path, ns, loops, repeats):
    try:
        if not isinstance(ns, list):
            raise ValueError("ns must be a list of integers")
        if len(ns) == 0:
            raise ValueError("ns must be non-empty")

        ns = [int(x) for x in ns]

        with open(code_path, "r", encoding="utf-8") as f:
            code_str = f.read()

        code_obj = compile(code_str, "<candidate>", "exec")

        # Warm-up: new sandbox each call so warmup state does not leak into measurements
        for n in ns:
            sandbox = {"__name__": "__main__"}
            exec(code_obj, sandbox)
            func = sandbox.get("calculator")
            if not callable(func):
                raise ValueError("No callable `calculator` found in sandbox.")
            func(n)

        repeat_totals_ns = []
        calls_per_repeat = loops * len(ns)

        for _ in range(repeats):
            gc.collect()
            total_ns = 0

            gc.disable()
            try:
                for _ in range(loops):
                    for n in ns:
                        sandbox = {"__name__": "__main__"}  # ✅ fresh sandbox every time (prevents cache tricks)
                        exec(code_obj, sandbox)
                        func = sandbox.get("calculator")
                        if not callable(func):
                            raise ValueError("No callable `calculator` found in sandbox.")

                        t0 = time.process_time_ns()
                        func(n)
                        total_ns += (time.process_time_ns() - t0)
            finally:
                gc.enable()

            repeat_totals_ns.append(total_ns)

        median_total_ns = int(statistics.median(repeat_totals_ns))
        avg_ns = median_total_ns / calls_per_repeat

        print(json.dumps({
            "ok": True,
            "total_ns": median_total_ns,
            "avg_ns": avg_ns,
            "repeats": repeats,
            "repeat_totals_ns": repeat_totals_ns,
            "ns_len": len(ns),
            "loops": loops,
        }))

    except Exception as e:
        import traceback
        gc.enable()
        print(json.dumps({
            "ok": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }))

if __name__ == "__main__":
    try:
        code_path = sys.argv[1]
        ns_arg = _read_json_arg(sys.argv[2])      # JSON list
        loops_arg = int(sys.argv[3])
        repeats_arg = int(sys.argv[4]) if len(sys.argv) > 4 else 7
        benchmark_main(code_path, ns_arg, loops_arg, repeats_arg)
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"Args error: {e}"}))
"""


def _run_python_script(
    script_path: str,
    args: List[str],
    timeout: float = 120.0,
    mem_gb: float = 0.5,
) -> Tuple[int, str, str]:
    """
    Executes a Python script in a subprocess, returning (returncode, stdout, stderr).
    Uses prlimit to limit the address space (default is 0.5GB).
    """
    mem_bytes = int(mem_gb * 1024**3)
    cmd = [
        "prlimit",
        f"--as={mem_bytes}",
        "--",
        sys.executable, script_path, *args
    ]
    # cmd = [
    #     sys.executable, script_path, *args
    # ]

    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = "1"
    env["MKL_NUM_THREADS"] = "1"
    env["OPENBLAS_NUM_THREADS"] = "1"
    env["VECLIB_MAXIMUM_THREADS"] = "1"
    env["NUMEXPR_NUM_THREADS"] = "1"
    try:
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as e:
        return -1, "", f"TimeoutExpired: {e}"


def require_prlimit() -> None:
    """The sandbox caps memory with ``prlimit --as``; fail early where it is unavailable (non-Linux)."""
    if shutil.which("prlimit") is None:
        raise EnvironmentError(
            "`prlimit` not found: the evaluation sandbox caps memory with it and needs Linux "
            "(util-linux). Run on Linux or install util-linux.")


class CandidateCodeValidator:
    """
    Decouple correctness and benchmark:
    - validate_correctness_batch: single subprocess, JSON carries all n values, returns all results
    - run_benchmark: measures runtime with a fresh sandbox per call to avoid cache interference
    """

    def __init__(self, timeout: float = 120.0, mem_gb: float = 0.5) -> None:
        self.timeout = float(timeout)
        self.mem_gb = float(mem_gb)

    def _write_temp_file(self, content: str, suffix: str) -> str:
        path: Optional[str] = None
        with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False, encoding="utf-8") as f:
            f.write(content)
            path = f.name
        return path

    def _safe_remove(self, path: Optional[str]) -> None:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

    def validate_correctness_batch(
        self,
        expected_output: List[int],
        offset_start: int,
        code_str: str,
        test_inputs: Optional[List[int]] = None,
    ) -> CodeValidationResult:
        """
        correctness: one subprocess receives all n values (generated or provided), returns all results at once for unified validation.
        - test_inputs=None: derive ns from ground_truth offset_start and expected_output
        - test_inputs!=None: allow custom ns, but they must align with expected_output
        """
        if not expected_output:
            print(
                f"[WARN] No expected_output provided; skipping correctness validation.", file=sys.stderr)
            return CodeValidationResult(ok=False, is_callable=False, error=None, tested_values=[])

        a0 = int(offset_start)
        expected_list = [int(x) for x in expected_output]
        num_tests = len(expected_list)

        # 1) Build/validate ns (expose only test_inputs, hide JSON details)
        if test_inputs is None:
            ns_list = [a0 + i for i in range(num_tests)]
        else:
            print(
                f"[INFO] Using provided test_inputs for correctness validation.", file=sys.stderr)
            if len(test_inputs) != num_tests:
                return CodeValidationResult(
                    ok=False,
                    is_callable=False,
                    error=f"test_inputs length mismatch: got {len(test_inputs)}, expected {num_tests}",
                    tested_values=[],
                )
            ns_list = [int(x) for x in test_inputs]

            # Keep original assumption: ground_truth uses consecutive n=a0+i
            # If you need to support non-consecutive n with aligned expected_output,
            # make this continuity check optional (kept strict here).
            for i, n in enumerate(ns_list):
                n_should_be = a0 + i
                if n != n_should_be:
                    return CodeValidationResult(
                        ok=False,
                        is_callable=False,
                        error=f"Input n mismatch at i={i}: got {n}, expected {n_should_be}",
                        tested_values=[],
                    )

        full_candidate_code = (
            RUNTIME_HEADER + "\n\n" + code_str).strip() + "\n"
        code_path: Optional[str] = None
        runner_path: Optional[str] = None
        ns_json_path: Optional[str] = None

        # 2) Build JSON args (internal detail) and automatically mitigate overly long CLI arguments
        #    Runner supports "@file" form: ns_arg.startswith("@") -> json.load(file)
        # conservative value to avoid approaching ARG_MAX / platform differences
        NS_ARG_INLINE_LIMIT = 32_000
        ns_json_str = json.dumps(ns_list)
        ns_arg = ns_json_str

        try:
            code_path = self._write_temp_file(
                full_candidate_code, suffix="_candidate.py")
            runner_path = self._write_temp_file(
                CORRECTNESS_BATCH_SCRIPT, suffix="_correctness.py")

            if len(ns_json_str) > NS_ARG_INLINE_LIMIT:
                ns_json_path = self._write_temp_file(
                    ns_json_str, suffix="_ns.json")
                ns_arg = f"@{ns_json_path}"

            rc, out, err = _run_python_script(
                runner_path,
                [code_path, ns_arg],
                timeout=self.timeout,
                mem_gb=self.mem_gb,
            )
            if rc != 0:
                return CodeValidationResult(
                    ok=False,
                    is_callable=False,
                    error=f"Correctness subprocess crash (rc={rc}). STDERR:\n{err}",
                    tested_values=[],
                )

            try:
                payload = json.loads(out.strip() or "{}")
            except json.JSONDecodeError:
                return CodeValidationResult(
                    ok=False,
                    is_callable=False,
                    error=("Failed to parse JSON from correctness subprocess.\n"
                           f"STDOUT:\n{out}\nSTDERR:\n{err}"),
                    tested_values=[],
                )

            if not payload.get("ok", False):
                msg = payload.get(
                    "error", "Correctness subprocess returned ok=false")
                tb = payload.get("traceback")
                if tb:
                    msg += "\n" + tb
                return CodeValidationResult(
                    ok=False,
                    is_callable=False,
                    error=msg,
                    tested_values=[],
                )

            results = payload.get("results", [])
            if not isinstance(results, list) or len(results) != num_tests:
                return CodeValidationResult(
                    ok=False,
                    is_callable=False,
                    error=f"Correctness results length mismatch: got {len(results) if isinstance(results, list) else 'non-list'}, expected {num_tests}",
                    tested_values=[],
                )

            # 3) Validate in one pass after all results are returned
            tested: List[Tuple[int, int, int]] = []
            for i in range(num_tests):
                n = ns_list[i]
                expected = expected_list[i]
                r = results[i]

                if not isinstance(r, dict):
                    return CodeValidationResult(
                        ok=False,
                        is_callable=True,
                        error=f"Invalid per-item result at n={n}: not a dict",
                        tested_values=tested,
                    )

                if int(r.get("n", n)) != n:
                    return CodeValidationResult(
                        ok=False,
                        is_callable=True,
                        error=f"Result n mismatch: got {r.get('n')}, expected {n}",
                        tested_values=tested,
                    )

                if not r.get("ok", False):
                    kind = r.get("kind")
                    if kind == "bool":
                        return CodeValidationResult(
                            ok=False,
                            is_callable=True,
                            error=f"calculator({n}) returned boolean {r.get('value')}, expected integer {expected}",
                            tested_values=tested,
                        )
                    if kind == "non-int":
                        return CodeValidationResult(
                            ok=False,
                            is_callable=True,
                            error=f"calculator({n}) returned non-integer value {r.get('repr')}",
                            tested_values=tested,
                        )
                    msg = r.get("error", "Unknown error in correctness batch")
                    tb = r.get("traceback")
                    if tb:
                        msg += "\n" + tb
                    return CodeValidationResult(
                        ok=False,
                        is_callable=True,
                        error=msg,
                        tested_values=tested,
                    )

                got_int = int(r.get("value", ''))
                tested.append((n, expected, got_int))
                if got_int != expected:
                    return CodeValidationResult(
                        ok=False,
                        is_callable=True,
                        error=f"Mismatch at n={n}: expected {expected}, got {got_int}",
                        tested_values=tested,
                    )

            return CodeValidationResult(
                ok=True,
                is_callable=True,
                error=None,
                tested_values=tested,
            )

        except Exception as e:
            tb = traceback.format_exc()
            return CodeValidationResult(
                ok=False,
                is_callable=False,
                error=f"Unexpected error in validate_correctness_batch: {e}\n{tb}",
                tested_values=[],
            )
        finally:
            self._safe_remove(code_path)
            self._safe_remove(runner_path)
            self._safe_remove(ns_json_path)

    def run_benchmark(
        self,
        code_str: str,
        ns: List[int],
        loops: int,
        repeats: int = 7,
    ) -> float:
        """
        Run benchmark independently over multiple n values and return average time per calculator call (ns).
        Fresh sandbox for every call to avoid cache contamination (core policy unchanged)
        """
        if not ns:
            raise ValueError("Benchmark ns must be non-empty")

        cleaned_ns: List[int] = []
        seen = set()
        for x in ns:
            ix = int(x)
            if ix not in seen:
                seen.add(ix)
                cleaned_ns.append(ix)

        if not cleaned_ns:
            raise ValueError("Benchmark ns became empty after cleaning")

        full_candidate_code = (
            RUNTIME_HEADER + "\n\n" + code_str).strip() + "\n"
        code_path: Optional[str] = None
        bench_path: Optional[str] = None

        try:
            code_path = self._write_temp_file(
                full_candidate_code, suffix="_candidate.py")
            bench_path = self._write_temp_file(
                BENCH_SCRIPT_TEMPLATE, suffix="_bench.py")

            rc, out, err = _run_python_script(
                bench_path,
                [code_path, json.dumps(cleaned_ns), str(
                    int(loops)), str(int(repeats))],
                timeout=self.timeout,
                mem_gb=self.mem_gb,
            )
            if rc != 0:
                raise RuntimeError(f"Benchmark process crash (rc={rc}): {err}")

            try:
                res = json.loads(out.strip() or "{}")
            except json.JSONDecodeError:
                raise RuntimeError(f"Benchmark output invalid JSON: {out}")

            if not res.get("ok"):
                raise RuntimeError(
                    f"Benchmark internal error: {res.get('error')}")

            return float(res["avg_ns"])

        finally:
            self._safe_remove(code_path)
            self._safe_remove(bench_path)

    def validate(
        self,
        expected_output: List[int],
        offset_start: int,
        code_str: str,
        runtime_test_nums: int = 20,
        bench_last_k: int = 7,
        repeats: int = 7,
    ) -> CodeValidationResult:
        """
        Main entry: run correctness first (batch JSON), then benchmark if it passes.
        """
        if not expected_output:
            print(
                f"[WARN] No expected_output provided; skipping correctness validation.", file=sys.stderr)
            return CodeValidationResult(ok=False, is_callable=False, error=None, tested_values=[])

        correctness_res = self.validate_correctness_batch(
            expected_output=expected_output,
            offset_start=offset_start,
            code_str=code_str,
        )

        if not correctness_res.ok:
            return correctness_res

        tested_ns = [x[0] for x in correctness_res.tested_values]
        if bench_last_k <= 0:
            bench_last_k = 1
        bench_ns = tested_ns[-bench_last_k:] if len(
            tested_ns) >= bench_last_k else tested_ns

        try:
            avg_elapsed = self.run_benchmark(
                code_str=code_str,
                ns=bench_ns,
                loops=int(runtime_test_nums),
                repeats=int(repeats),
            )
        except Exception as bench_err:
            return CodeValidationResult(
                ok=False,
                is_callable=True,
                error=f"Correctness passed, but benchmark failed: {bench_err}",
                tested_values=correctness_res.tested_values,
            )

        return CodeValidationResult(
            ok=True,
            is_callable=True,
            elapsed_time=avg_elapsed,
            error=None,
            tested_values=correctness_res.tested_values,
        )


# Backward compatible signature: external callers keep existing API (internally delegates to class implementation)
def validate_candidate_code(
    expected_output: List[int],
    offset_start: int,
    code_str: str,
    runtime_test_nums: int = 50,
    bench_last_k: int = 7,
    timeout: float = 300.0,
    mem_gb: float = 0.5,
) -> CodeValidationResult:
    """Public entrypoint: validate code given ground-truth outputs and offset."""
    validator = CandidateCodeValidator(timeout=timeout, mem_gb=mem_gb)
    return validator.validate(
        expected_output=expected_output,
        offset_start=offset_start,
        code_str=code_str,
        runtime_test_nums=runtime_test_nums,
        bench_last_k=bench_last_k,
        repeats=7,
    )
