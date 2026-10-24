import json
import re
from typing import Any, Dict, List


def extract_code_from_response(text: str) -> str:
    python_blocks = re.findall(r"```python\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if python_blocks:
        return max(python_blocks, key=len).strip()

    return text.strip()


def normalize_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: List[str] = []
        for item in content:
            if isinstance(item, dict):
                chunks.append(str(item.get("text", "")))
            else:
                chunks.append(str(item))
        return "".join(chunks)
    return str(content)


def sanitize_model_name(model_name: str) -> str:
    # Replace characters unsafe for file paths.
    return re.sub(r"[^\w.-]+", "_", model_name).strip("_") or "model"


def write_output_file(
    output_path: str,
    model_name: str,
    max_tokens: int,
    result_code: Dict[str, Dict[str, str]],
    evaluation_results: List[Dict[str, Any]],
    raw_responses: Dict[str, Dict[str, str]],
    metrics: Dict[str, Any] | None = None,
) -> None:
    payload = {
        "model_name": model_name,
        "max_tokens": max_tokens,
        "result_code": result_code,
        "evaluation_results": evaluation_results,
        "raw_responses": raw_responses,
        "metrics": metrics,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
