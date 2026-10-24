from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any


@dataclass
class CodeValidationResult:
    def __init__(
        self,
        ok: bool,
        is_callable: bool,
        error: Optional[str] = None,
        tested_values: Optional[List[Tuple[int, int, int]]] = None,
        elapsed_time: float = 0.0,
    ) -> None:
        self.ok = ok
        self.is_callable = is_callable
        self.error = error
        self.tested_values = tested_values or []
        self.elapsed_time = elapsed_time

@dataclass
class ResultsWithVerdict:
    """
    Docstring for ResultsWithVerdict.
    Results revalidated and stored here.
    """
    index: str
    inefficientCode: str
    inefficientRuntime: Optional[float]
    referenceCode: str
    referenceRuntime: Optional[float]
    respCode: Dict[str, str] = field(default_factory=dict)
    respRuntime: Dict[str, float] = field(default_factory=dict)
    respVerdict: Dict[str, bool] = field(default_factory=dict)
    label: Optional[str] = field(default=None)
