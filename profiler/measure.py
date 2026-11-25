#!/usr/bin/env python3
"""Nosana Cold-Start Profiler — measurement harness.

Standard library only. No Nosana credentials needed for --dry-run.

What is measured per combo (all in seconds, from the moment the
deployment is posted):
  schedule_s        post -> deployment first reports RUNNING
  image_pull_s      RUNNING -> first container log line (image ready)
  model_download_s  image ready -> model ready marker (see combos.json)
  ttft_s            model ready -> first token / first output
                    (combo 04 Whisper is a batch job: this column is
                    time-to-first-transcript instead)
  cache             hit  = same node served the job before (image/model
                           already on that node) / miss = cold node.
                           Determined by comparing the node address of
                           this run with previous runs in runs/.
  cost_usd          total runtime x market $/h, from the Nosana
                    dashboard figure for that run — never estimated
                    from a price table alone.

Usage:
  python3 measure.py --list
      List the 5 combos.
  python3 measure.py --dry-run
      Validate combos.json + all 5 job-definitions, check the log
      marker parsers against embedded sample log lines, and write
      runs/dry-run-<timestamp>.json with every timing null and
      status "not_measured". No network, no GPU, no fake timings.
  python3 measure.py --combo 01-ollama-qwen3-8b-4090 --live
      Refuses (exit 2): a live run needs a Nosana account, API key
      (nos_...), and the $70 credits. It must never be run blind.
      Prints the exact steps instead.

Honest rule enforced by this script: a timing field is either a
real number with a run file to back it, or null. There is no
estimated / typical / expected timing anywhere in the output.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
COMBOS_PATH = HERE / "combos.json"
JOB_DEFS = HERE / "job-definitions"
RUNS_DIR = HERE / "runs"

# Log-line markers, one per serving stack. Each is a substring that
# appears in real container logs when that stage is reached.
# Tested in --dry-run against SAMPLE_LOGS below.
MARKERS = {
    "ollama_model_ready": "success",          # ollama pull prints "success"
    "vllm_ready": "Uvicorn running on",       # vLLM OpenAI server ready
    "comfyui_ready": "To see the GUI go to",  # ComfyUI listening
    "whisper_done": ".txt",                   # transcript file written
}

SAMPLE_LOGS = {
    "ollama_model_ready": "pulling 8b1e1c1e1c1e... 100%\nsuccess\n",
    "vllm_ready": "INFO:     Uvicorn running on http://0.0.0.0:9000\n",
    "comfyui_ready": "To see the GUI go to: http://0.0.0.0:8188\n",
    "whisper_done": "Detected language: en\n/root/out/sample.txt\n",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_combos() -> list[dict]:
    with COMBOS_PATH.open() as f:
        data = json.load(f)
    return data["combos"]


def validate_job_definition(job: dict, combo: dict) -> list[str]:
    errors: list[str] = []
    if job.get("version") != "0.1":
        errors.append(f"version must be '0.1' (got {job.get('version')!r})")
    ops = job.get("ops")
    if not isinstance(ops, list) or not ops:
        errors.append("ops must be a non-empty list")
        return errors
    args = ops[0].get("args", {}) if isinstance(ops[0], dict) else {}
    if args.get("gpu") is not True:
        errors.append("args.gpu must be true")
    if not isinstance(args.get("image"), str) or not args["image"]:
        errors.append("args.image must be a non-empty string")
    if args.get("image") != combo["image"]:
        errors.append(f"image {args.get('image')!r} != combos.json "
                      f"{combo['image']!r}")
    dep = job.get("deployment", {}) if isinstance(job.get("deployment"), dict) else {}
    if dep.get("strategy") != "SIMPLE":
        errors.append(f"strategy must be SIMPLE (got {dep.get('strategy')!r})")
    if dep.get("replica_count") != 1:
        errors.append(f"replica_count must be 1 (got {dep.get('replica_count')!r})")
    timeout = dep.get("container_timeout_minutes")
    if not isinstance(timeout, (int, float)) or not (30 <= timeout <= 60):
        errors.append(f"container_timeout_minutes must be 30–60 (got {timeout!r})")
    if dep.get("market_address") != combo["gpu_market_address"]:
        errors.append("market_address != combos.json")
    if dep.get("market_slug") != combo["gpu_market_slug"]:
        errors.append("market_slug != combos.json")
    if dep.get("min_vram_gb", 0) < combo["min_vram_gb"]:
        errors.append("min_vram_gb below combo requirement")
    return errors


def find_marker(log_text: str) -> str | None:
    """Return the first marker key found in log text, else None."""
    for key, marker in MARKERS.items():
        if marker in log_text:
            return key
    return None


def cmd_list(combos: list[dict]) -> int:
    for c in combos:
        cached = "image CACHED on market" if c["image_precached_on_market"] \
            else "image NOT cached — cold pull"
        print(f"{c['id']:28} {c['serving']:28} {c['gpu']:10} {cached}")
    return 0


def cmd_dry_run(combos: list[dict]) -> int:
    errors: list[str] = []
    print("profiler --dry-run: validating combos + job-definitions")
    for combo in combos:
        path = HERE / combo["job_definition"]
        if not path.is_file():
            errors.append(f"{combo['id']}: missing {combo['job_definition']}")
            continue
        try:
            job = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            errors.append(f"{combo['id']}: INVALID JSON — {exc}")
            continue
        job_errors = validate_job_definition(job, combo)
        if job_errors:
            errors += [f"{combo['id']}: {e}" for e in job_errors]
        else:
            dep = job["deployment"]
            print(f"  OK {combo['id']} — {combo['serving']}, "
                  f"{dep['gpu_model']}, {dep['strategy']}, "
                  f"replicas={dep['replica_count']}, "
                  f"timeout={dep['container_timeout_minutes']} min, "
                  f"image {'CACHED' if combo['image_precached_on_market'] else 'COLD'}")
    print("checking log markers against sample log lines:")
    for key, sample in SAMPLE_LOGS.items():
        found = find_marker(sample)
        status = "OK" if found == key else f"FAILED (found {found!r})"
        print(f"  {key:22} {status}")
        if found != key:
            errors.append(f"marker {key} not detected in sample log")
    if errors:
        print("dry-run: FAILED")
        for e in errors:
            print(f"  - {e}")
        return 1
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now_iso().replace(":", "").replace("-", "")
    record = {
        "status": "not_measured",
        "generated_at_utc": now_iso(),
        "note": "Dry run only. No Nosana deployment was made, no GPU "
                "was used, every timing below is null. Fill it in by "
                "running reproduce.sh with live credits.",
        "combos": [
            {"id": c["id"], "schedule_s": None, "image_pull_s": None,
             "model_download_s": None,
             "ttft_s": None, "cache": None, "cost_usd": None,
             "node": None, "deployment_id": None}
            for c in combos
        ],
    }
    path = RUNS_DIR / f"dry-run-{stamp}.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    print(f"dry-run record written: {path} (all timings null, not_measured)")
    print("dry-run: PASSED")
    return 0


def cmd_live(combo_id: str) -> int:
    print("live measurement is NOT run from this script yet.")
    print()
    print("A live run needs all of these first:")
    print("  1. Nosana $70 credits approved (awaiting the approval email)")
    print("  2. A Nosana API key (nos_...) — never commit one to this repo")
    print("  3. The market for the combo actually having a free host —")
    print("     check first:  python3 ../scout.py --once")
    print("  4. For combo 04 (Whisper): a real 30-second speech WAV at")
    print("     /root/sample.wav — a synthetic tone is not a valid probe")
    print()
    print("Then run one combo only (cheapest first):")
    print(f"  bash reproduce.sh {combo_id}")
    print("and record the real numbers in profiler/RESULTS.md.")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nosana Cold-Start Profiler")
    parser.add_argument("--list", action="store_true", help="List combos")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate everything, measure nothing")
    parser.add_argument("--live", action="store_true",
                        help="Measure a combo live (not implemented yet)")
    parser.add_argument("--combo", type=str, default=None,
                        help="Combo id for --live")
    args = parser.parse_args(argv)

    try:
        combos = load_combos()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"cannot load combos.json: {exc}")
        return 1

    if args.list:
        return cmd_list(combos)
    if args.dry_run:
        return cmd_dry_run(combos)
    if args.live:
        if not args.combo:
            print("--live needs --combo <id> (see --list)")
            return 2
        if args.combo not in [c["id"] for c in combos]:
            print(f"unknown combo: {args.combo}")
            return 2
        return cmd_live(args.combo)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
