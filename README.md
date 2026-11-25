# Nosana Market Scout + Cold-Start Profiler

Open-source observability tooling for the Nosana GPU marketplace.
Two parts, one repo:

- **Scout** — snapshots live GPU availability + prices from the
  public Nosana API, and warns before you deploy into an empty
  market.
- **Profiler** — measures cold-start time (schedule, image pull,
  model download, time to first token) for 5 real workloads, with
  reproducible job definitions.

Built for the **Decentralize AI Hackathon by HackerNoon** (Round 2,
Nov 2026 – Feb 2027), category: **decentralized GPU orchestration +
verifiable AI**.

> **Status: Scout works live today. Profiler has not run live yet.**
> `scout.py --once` fetched a real snapshot on 25 Nov 2026 (48
> markets, 25 GPU types). No profiler timing has been measured —
> every timing in `profiler/RESULTS.md` is `NOT MEASURED` until a
> live run with credits happens. See [Limitations](#limitations).

## The problem (3 facts, all sourced)

1. **Demand for RTX 4090/5090 outpaces supply** (Nosana Monthly,
   Oct 2026). Builders waste time deploying into markets with no
   free hosts.
2. **Cold start is the bottleneck.** Nosana docs: "the main bottle
   neck to spinning up a Nosana Job is downloading the assets."
   Cache lasts 24h and only on the same node — a second job on a
   different node downloads everything again.
3. **The API hides failures.** Credits only work on PREMIUM markets,
   but a COMMUNITY-market job fails async while the API still
   reports the deployment as RUNNING. Logs live only in the raw
   IPFS result; the API returns empty log arrays.

## Quick start

Python 3.10+, stdlib only. No dependencies, no API key.

```bash
# Scout: fetch a live snapshot (public API, no key)
python3 scout.py --once
python3 scout.py --check          # validate snapshots/latest.json
python3 scout.py --dry-run        # offline check with sample data

# Profiler: validate combos + job definitions (measures nothing)
python3 profiler/measure.py --list
python3 profiler/measure.py --dry-run

# Dashboard (serve it so it can fetch the snapshot)
python3 -m http.server 8000
# open http://localhost:8000/dashboard/
```

## What the scout found on 25 Nov 2026 (live)

From `snapshots/latest.json` (run `scout.py --once` for a fresh one):

| GPU | Free / Nodes | Price $/h |
|---|---|---|
| RTX 4090 | 13 free / 71 | $0.10–0.36 |
| RTX 5090 | 19 free / 48 | $0.45 |
| RTX 3090 | 13 free / 42 | $0.17 |
| RTX 3060 | **0 free / 7 — DO NOT DEPLOY** | $0.04 |
| A100 40GB / 80GB, H100 | **Not in availability data — unknown, not zero** | — |

Cheapest PREMIUM market: ≥12GB VRAM → nvidia-4070 $0.0873/h;
≥24GB → nvidia-3090 $0.1745/h; ≥32GB → nvidia-a40 $0.3636/h.
(Checked live; prices change — re-run the scout on deploy day.)

Two findings worth knowing:

- `/markets` returns 48 markets with prices and pre-cached images,
  but its `nodes` field is always empty — it cannot tell you free
  hosts. Availability comes only from `/reservations/gpus`.
- `/nodes` and `/markets/<addr>/required-resources` need an
  Authorization header (HTTP 422 without one). The scout never
  sends a key and never fakes that data.

## Profiler: the 5 combos

| # | Workload | GPU | Image pre-cached on its market? |
|---|---|---|---|
| 01 | Ollama qwen3:8b | 4090 | Yes (`ollama:0.15.4`) |
| 02 | vLLM Qwen2.5-7B-Instruct | 3090 | Yes (`vllm-openai:v0.10.2`) |
| 03 | ComfyUI FLUX.1-schnell FP8 | 5090 | Base image yes; ComfyUI is git-cloned at start (no ComfyUI image is pre-cached anywhere) |
| 04 | Whisper large-v3 (batch) | 3090 | Base image yes; `pip install openai-whisper` at start |
| 05 | vLLM Nanonets-OCR2-3B | 4090 | Yes (`vllm-openai:v0.10.2`) |

All job definitions: SIMPLE, 1 replica, 60-min timeout.
Full method + empty results table: `profiler/RESULTS.md`.
One-combo reproduction: `bash profiler/reproduce.sh <combo-id>`.

## Review checklist (Decentralize AI Hackathon)

- [x] Working code: `scout.py` fetches live public API data;
  `measure.py --dry-run` validates all 5 job definitions.
- [x] Real data, no mockups: the snapshot in `snapshots/` is a
  real live response (25 Nov 2026). The `--dry-run` sample is
  labelled SAMPLE and never presented as live.
- [x] Category: decentralized GPU orchestration + verifiable AI
  (reproducible measurements).
- [x] Verifiable evidence: snapshot JSON + dashboard + `RESULTS.md`
  method + `reproduce.sh` for one-command re-runs.
- [ ] Live profiler runs (needs credits) — see below.
- [ ] HackerNoon write-up (required for a full hackathon submission).

## Limitations (honest)

- **No profiler timing measured yet.** All timings are null.
  The $1.53 "all 5 at full timeout" figure is a price-table
  calculation, not a measurement.
- **Availability is a point in time.** A snapshot is stale minutes
  later. Always re-run `scout.py --once` before deploying.
- **A100/H100 availability is unknown**, not zero: they exist in
  `/markets` but do not appear in `/reservations/gpus`.
- **Combo 03/04 setup dominates cold start** (git clone / pip
  install). That is the finding to measure, not a flaw to hide.
  Combo 04 also needs a real 30-second speech WAV; without it the
  run is invalid.
- **Prices change.** The 4090 "reserved" market ($0.10/h, 999GB
  VRAM) is excluded from cheapest-pick: its VRAM value is not a
  real card class.
- **Arweave upload of snapshots is planned, not done.** No TX
  exists for this project.

## Live runs (TODO, needs credits)

1. `python3 scout.py --once` — confirm a free host.
2. Run combo 02 first (cheapest), then 01. One combo per run.
3. Record the 5 timings + node + cost in `profiler/RESULTS.md`,
   save the raw log to `profiler/runs/`.
4. Never deploy into a 0-free-host market; never use a COMMUNITY
   market with credits.

## Files

```
scout.py                    live availability snapshots (stdlib only)
dashboard/index.html        static dashboard, reads snapshots/latest.json
snapshots/latest.json       latest live snapshot (25 Nov 2026)
profiler/combos.json        the 5 combos (images verified pre-cached)
profiler/job-definitions/   5 Nosana job definitions (SIMPLE, 1 replica)
profiler/measure.py         validation + dry-run harness (measures nothing live)
profiler/RESULTS.md         method + empty measured table + cost ceiling
profiler/reproduce.sh       one-command re-run for one combo
```

## License

MIT — see [`LICENSE`](LICENSE).
