<h1 align="center">OEISPerf</h1>

<p align="center">
  <b>Beyond Correctness: Benchmarking LLMs for Algorithmic Program Optimization</b><br>
  <i>Findings of EMNLP 2026</i>
</p>

<p align="center">
  <a href="https://aclanthology.org/2026.findings-emnlp.152/"><img alt="Paper" src="https://img.shields.io/badge/%F0%9F%93%84%20Paper-ACL%20Anthology-B31B1B?style=for-the-badge"></a>
  <a href="https://huggingface.co/datasets/l-yh/OEISPerf"><img alt="Dataset" src="https://img.shields.io/badge/%F0%9F%A4%97%20Dataset-l--yh%2FOEISPerf-FFD21E?style=for-the-badge"></a>
  <a href="#citation"><img alt="BibTeX" src="https://img.shields.io/badge/%F0%9F%93%8B%20BibTeX-Cite-4C78DD?style=for-the-badge"></a>
</p>

OEISPerf is a code-to-code benchmark for algorithmic program optimization, built from alternative implementations of integer sequences in the [OEIS](https://oeis.org). Each instance pairs a slow (*inefficient*) Python program with the fastest validated *reference* for the same sequence. The model sees only the slow program and must return a faster `calculator(n: int) -> int`.

This repository contains the evaluation code used in the paper: it prompts a model, validates the generated programs, and reports the three metrics of the paper.

## 📦 Dataset

The data lives on the Hugging Face Hub: [`l-yh/OEISPerf`](https://huggingface.co/datasets/l-yh/OEISPerf).

| | train | validation | test |
| :--- | ---: | ---: | ---: |
| **Instances** | 2,762 | 240 | 265 |

| Field | Description |
| --- | --- |
| `id` | Instance id, e.g. `test_0` |
| `inefficientCode` | Slow program, the only input shown to the model |
| `referenceCode` | Efficient reference program (**encrypted** on the Hub) |
| `expected_output` | Ground-truth terms as decimal strings (**encrypted** on the Hub) |
| `offset_start` | Index of the first term: `calculator(offset_start + i)` must equal `expected_output[i]` |
| `termSize` | Number of terms |

> [!NOTE]
> `referenceCode` and `expected_output` are stored **encrypted** on the Hub. `OEISPerfLoader` (`utils/loadData.py`) decrypts them in memory when you load a split. Please do not republish the decrypted data.

```python
from utils import OEISPerfLoader

df = OEISPerfLoader.load("test")   # "train" | "validation" | "test"; returns a pandas DataFrame
print(df.shape, df.columns.tolist())
```

## ⚙️ Setup

> [!IMPORTANT]
> **Linux is required to run the evaluation.** Candidate programs run in subprocesses whose memory is capped with `prlimit` (util-linux). Loading the dataset works on any OS.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 🚀 Evaluate a model

```bash
python main.py \
  --model "gpt-5.2-2025-12-11" \
  --apikey YOUR_API_KEY \
  --baseurl "https://api.openai.com/v1"
```

Any OpenAI-compatible chat endpoint works. The defaults reproduce the paper's setting: test split, one sample per instance, temperature 1.0, at most 32,768 output tokens. Results (raw responses, extracted code, per-instance verdicts and runtimes, summary metrics) are written to `results/<model>.json`.

| Flag | Description |
| --- | --- |
| `--model` | Model name sent to the endpoint |
| `--apikey` | API key; defaults to `OPENAI_API_KEY` |
| `--baseurl` | Endpoint; defaults to `OPENAI_BASE_URL` / `OPENAI_API_BASE` |
| `--maxtokens` | Maximum output tokens (default 32768) |
| `--temperature` | Sampling temperature (default 1.0) |
| `--extra-body` | JSON added to the request body for provider-specific options such as the thinking mode, e.g. `'{"enable_thinking": true}'` or `'{"reasoning_effort": "medium"}'` |
| `--split` | `test` (default), `validation` or `train` |
| `--limit` | Use only the first N instances (quick smoke test) |

## 🙏 Data source

The sequences come from the [OEIS](https://oeis.org), which is distributed under CC BY-SA 4.0.

<a id="citation"></a>

## 📚 Citation

If you find this repository or the dataset helpful, please cite our paper:

```bibtex
@inproceedings{lin-chen-2026-beyond,
    title = "Beyond Correctness: Benchmarking {LLM}s for Algorithmic Program Optimization",
    author = "Lin, Yehua  and
      Chen, Yin",
    editor = "Lee, Hung-Yi  and
      Dev, Sunipa  and
      Li, Jessy  and
      Elliott, Desmond",
    booktitle = "Findings of the {A}ssociation for {C}omputational {L}inguistics: {EMNLP} 2026",
    month = oct,
    year = "2026",
    address = "Budapest, Hungary",
    publisher = "Association for Computational Linguistics",
    url = "https://aclanthology.org/2026.findings-emnlp.152/",
    pages = "3211--3226",
    ISBN = "979-8-89176-462-0",
    abstract = "Large language models (LLMs) can generate functionally correct code, but their ability to optimize programs is underexplored. We introduce OEISPerf, a code-to-code benchmark for algorithmic program optimization built from alternative implementations of integer sequences in the On-Line Encyclopedia of Integer Sequences. For each sequence we mine multiple mathematical formulations, synthesize and validate Python implementations, and pair a substantially slower program with the fastest validated reference found by our construction pipeline, producing instances that often require substantial structural transformations and can yield substantial speedups. Evaluating 15 open-source and closed-source LLMs shows that even the strongest evaluated setting, Gemini-3-Flash with its thinking mode enabled, matches the reference efficiency on only 67.9{\%} of test instances. Across LLMs, we observe both incorrect ``optimizations'' that break correctness and superficial refactorings that preserve suboptimal complexity. Finally, we fine-tune Qwen2.5-Coder-3B-Instruct via reinforcement learning, markedly improving reference attainment on OEISPerf while slightly improving performance on standard code-generation benchmarks."
}
```
