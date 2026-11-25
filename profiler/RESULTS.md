# Cold-Start Results — Nosana Market Scout + Profiler

## Status: NOTHING MEASURED YET

No live Nosana deployment has been run for this project. Every timing
cell below is `NOT MEASURED`. The "Calculated" section is arithmetic
from live market prices — not a measurement. Do not quote it as
measured.

## Method (what a real run records)

Per combo, from the moment the deployment is posted:

| Field | Meaning |
|---|---|
| `schedule_s` | post -> deployment first reports RUNNING |
| `image_pull_s` | RUNNING -> first container log line |
| `model_download_s` | image ready -> ready marker (see `combos.json`) |
| `ttft_s` | model ready -> first token / first output. Combo 04 (Whisper, batch job): time-to-first-transcript instead |
| `cache` | `hit` = same node served the job before (image/model already on that node); `miss` = cold node. Determined by comparing the node address with previous runs in `runs/` |
| `cost_usd` | Cost shown by Nosana for that run — never a price-table estimate alone |

Rules:

- One combo per run. Cheapest first (02, then 01).
- Check the market first: `python3 ../scout.py --once` — do not
  deploy into a market with 0 free hosts.
- If a run fails (image pull error, model download timeout, OOM),
  record the failure and the reason. A failed run is data, not a
  reason to re-run silently until it passes.
- Combo 04 needs a real 30-second speech WAV at `/root/sample.wav`.
  A synthetic tone is not a valid probe; without a real sample the
  run is `NOT MEASURED`.
- Save the raw log to `runs/<combo>-<date>.log`.

## The 5 combos

| # | Combo | GPU | Image (pre-cached?) | Timeout |
|---|---|---|---|---|
| 01 | Ollama qwen3:8b | RTX 4090 | `ollama:0.15.4` — yes, on nvidia-4090 | 60 min |
| 02 | vLLM Qwen2.5-7B-Instruct | RTX 3090 | `vllm-openai:v0.10.2` — yes, on nvidia-3090 | 60 min |
| 03 | ComfyUI FLUX.1-schnell FP8 | RTX 5090 | TensorFlow CUDA base — yes; ComfyUI itself is git-cloned at start (no ComfyUI image is pre-cached in any market) | 60 min |
| 04 | Whisper large-v3 (batch) | RTX 3090 | TensorFlow CUDA base — yes; `pip install openai-whisper` at start | 60 min |
| 05 | vLLM Nanonets-OCR2-3B | RTX 4090 | `vllm-openai:v0.10.2` — yes, on nvidia-4090 | 60 min |

Image pre-cache status verified from the live `/markets` response,
25 Nov 2026 (see `../snapshots/latest.json`). Model weights are NOT
pre-cached in any combo — their download time is what is measured.

## Measured — TODO after live runs

| Combo | schedule_s | image_pull_s | model_download_s | ttft_s | cache | cost_usd | node | date (UTC) |
|---|---|---|---|---|---|---|---|---|
| 01-ollama-qwen3-8b-4090 | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED | — | — | — | — |
| 02-vllm-qwen2.5-7b-3090 | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED | — | — | — | — |
| 03-comfyui-flux-5090 | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED | — | — | — | — |
| 04-whisper-large-v3-3090 | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED (transcript time) | — | — | — | — |
| 05-vllm-nanonets-ocr-4090 | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED | — | — | — | — |

## Calculated — cost ceiling only (not measured)

Live market prices from `../snapshots/latest.json` (25 Nov 2026):

| Market | $/hour |
|---|---|
| nvidia-4090 (PREMIUM) | 0.3636 |
| nvidia-3090 (PREMIUM) | 0.1745 |
| nvidia-5090 (PREMIUM) | 0.4545 |

If every combo ran the full 60-minute timeout (worst case, it
should not — a run ends when the probe succeeds):

| Combo | 60-min ceiling |
|---|---|
| 01 (4090) | $0.36 |
| 02 (3090) | $0.17 |
| 03 (5090) | $0.45 |
| 04 (3090) | $0.17 |
| 05 (4090) | $0.36 |
| **Total, all 5 at full timeout** | **$1.53** |

Realistic total for 5 runs of ~10–20 minutes each: **~$0.50–0.80**.
Well within $70 — the point of the profiler is to prove that with
numbers, not to spend the credits.

## How to reproduce (one combo)

```bash
bash profiler/reproduce.sh 01-ollama-qwen3-8b-4090
```

Then create the deployment from
`profiler/job-definitions/01-ollama-qwen3-8b-4090.json` in the Nosana
dashboard, record the five timings + node + cost, save the raw log to
`profiler/runs/`, and fill in the Measured table above.
