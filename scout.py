#!/usr/bin/env python3
"""Nosana Market Scout — live GPU availability snapshots from the
public Nosana API. Standard library only, no API key needed.

What it reads (all public, no authentication):
  GET https://api.nosana.com/markets
      48 markets (25 Nov 2026). Gives slug, name, address, type
      (PREMIUM / COMMUNITY / OTHER), usd_reward_per_hour, VRAM
      metadata, and required_images (images pre-cached on nodes in
      that market). NOTE: the `nodes` field in this response is always
      an empty list — it does NOT tell you free hosts. Do not use it
      for availability.
  GET https://api.nosana.com/reservations/gpus
      The real availability source. Per GPU model: nodes, available
      (free right now), working, price min/max USD per hour, and the
      market addresses that serve it. A100 / H100 do not appear in
      this response at all (25 Nov 2026) even though they exist in
      /markets — availability for them is UNKNOWN, not zero.

What it deliberately does NOT read:
  GET /nodes, GET /markets/<addr>/required-resources — both return
  HTTP 422 asking for an Authorization header. They need a Nosana
  API key (nos_...). The scout never sends a key and never fakes
  the data those endpoints would return.

Usage:
  python3 scout.py --once
      Fetch live data, print a summary, write
      snapshots/<UTC-timestamp>.json and snapshots/latest.json.
  python3 scout.py --check [--file snapshots/latest.json]
      Validate a snapshot file against the schema. Exits 0 on OK.
  python3 scout.py --dry-run
      No network. Runs the same builder + validator against a tiny
      embedded SAMPLE (labelled as sample, never presented as live).
  python3 scout.py --once --out-dir /some/dir
      Write snapshots somewhere else.

Credit rule (from the Nosana claim page + community skill verified
against the live API): credits only work on PREMIUM markets. A
COMMUNITY market fails asynchronously for credit-funded jobs while
the API still reports the deployment as RUNNING. The scout therefore
excludes COMMUNITY markets from the "cheapest PREMIUM" pick and
flags them in the snapshot.
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
API_BASE = "https://api.nosana.com"
MARKETS_URL = f"{API_BASE}/markets"
GPUS_URL = f"{API_BASE}/reservations/gpus"
SCHEMA_VERSION = 1
TIMEOUT_SECONDS = 20

# GPU classes the scout tracks. `gpus_name` is the exact name in
# /reservations/gpus; `market_slug_prefix` matches /markets slugs.
FOCUS_GPUS = [
    {"key": "RTX 4090", "gpus_name": "NVIDIA GeForce RTX 4090",
     "market_slug_prefix": "nvidia-4090", "min_vram_gb": 24},
    {"key": "RTX 5090", "gpus_name": "NVIDIA GeForce RTX 5090",
     "market_slug_prefix": "nvidia-5090", "min_vram_gb": 32},
    {"key": "RTX 3090", "gpus_name": "NVIDIA GeForce RTX 3090",
     "market_slug_prefix": "nvidia-3090", "min_vram_gb": 24},
    {"key": "RTX 3060", "gpus_name": "NVIDIA GeForce RTX 3060",
     "market_slug_prefix": "nvidia-3060", "min_vram_gb": 12},
    {"key": "A100 40GB", "gpus_name": "NVIDIA A100 40GB",
     "market_slug_prefix": "nvidia-a100-40gb", "min_vram_gb": 40},
    {"key": "A100 80GB", "gpus_name": "NVIDIA A100 80GB",
     "market_slug_prefix": "nvidia-a100-80gb", "min_vram_gb": 80},
    {"key": "H100", "gpus_name": "NVIDIA H100",
     "market_slug_prefix": "nvidia-h100", "min_vram_gb": 80},
]

# Tiny embedded sample for --dry-run only. Labelled sample, never
# presented as live data. Mirrors the real shapes (subset).
SAMPLE_MARKETS = [
    {"address": "97G9NnvBDQ2WpKu6fasoMsAKmfj63C9rhysJnkeWodAf",
     "slug": "nvidia-4090", "name": "NVIDIA 4090", "type": "PREMIUM",
     "usd_reward_per_hour": 0.3636,
     "metadata": [{"key": "vram", "value": "24GB"}],
     "required_images": ["docker.io/ollama/ollama:0.32.6"], "nodes": []},
    {"address": "Dcwz62TiX6jX8YF7vJ8vJ8vJ8vJ8vJ8vJ8vJ8vJ8vJ8",
     "slug": "nvidia-4090-community", "name": "NVIDIA 4090 Community",
     "type": "COMMUNITY", "usd_reward_per_hour": 0.3636,
     "metadata": [], "required_images": [], "nodes": []},
]
SAMPLE_GPUS = {"gpus": [
    {"name": "NVIDIA GeForce RTX 4090", "vramGb": 24, "nodes": 71,
     "available": 12, "working": 37,
     "markets": ["97G9NnvBDQ2WpKu6fasoMsAKmfj63C9rhysJnkeWodAf"],
     "price": {"minUsdPerHour": 0.1, "maxUsdPerHour": 0.3636}},
    {"name": "NVIDIA GeForce RTX 3060", "vramGb": 12, "nodes": 7,
     "available": 0, "working": 6,
     "markets": ["7AtiXMSH"],
     "price": {"minUsdPerHour": 0.0436, "maxUsdPerHour": 0.0436}},
]}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_json(url: str) -> tuple[object | None, int | None, str | None]:
    """GET a JSON URL. Returns (data, http_status, error)."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "nosana-market-scout/1.0",
                      "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8")), resp.status, None
    except urllib.error.HTTPError as exc:
        return None, exc.code, f"HTTP {exc.code} from {url}"
    except urllib.error.URLError as exc:
        return None, None, f"network error fetching {url}: {exc.reason}"
    except (json.JSONDecodeError, TimeoutError) as exc:
        return None, None, f"bad response from {url}: {exc}"


def parse_vram_gb(metadata: object) -> int | None:
    """Parse [{'key': 'vram', 'value': '24GB'}] -> 24. None if absent."""
    if not isinstance(metadata, list):
        return None
    for item in metadata:
        if isinstance(item, dict) and item.get("key") == "vram":
            match = re.search(r"(\d+)", str(item.get("value", "")))
            if match:
                return int(match.group(1))
    return None


def build_snapshot(markets_raw: list, gpus_raw: dict,
                   source_status: dict) -> dict:
    """Build a snapshot from raw /markets + /reservations/gpus data."""
    markets: list[dict] = []
    for m in markets_raw:
        if not isinstance(m, dict):
            continue
        images = m.get("required_images") or []
        markets.append({
            "slug": m.get("slug"),
            "name": m.get("name"),
            "address": m.get("address"),
            "type": m.get("type"),
            "usd_per_hour": m.get("usd_reward_per_hour"),
            "vram_gb": parse_vram_gb(m.get("metadata")),
            "required_images": images,
            "cached_ollama": any("ollama/ollama" in i for i in images),
            "cached_vllm": any("vllm/vllm-openai" in i for i in images),
        })

    gpus: list[dict] = []
    for g in (gpus_raw.get("gpus") or []):
        if not isinstance(g, dict):
            continue
        price = g.get("price") or {}
        gpus.append({
            "name": g.get("name"),
            "vram_gb": g.get("vramGb"),
            "nodes": g.get("nodes"),
            "available": g.get("available"),
            "working": g.get("working"),
            "price_min_usd_per_hour": price.get("minUsdPerHour"),
            "price_max_usd_per_hour": price.get("maxUsdPerHour"),
            "market_addresses": g.get("markets") or [],
        })

    by_name = {g["name"]: g for g in gpus}
    by_slug = {m["slug"]: m for m in markets}

    focus: list[dict] = []
    warnings: list[str] = []
    for f in FOCUS_GPUS:
        entry: dict = {"gpu": f["key"], "min_vram_gb": f["min_vram_gb"]}
        g = by_name.get(f["gpus_name"])
        # Fallback: substring match (API names vary, e.g. "A100 40GB"
        # may appear as "NVIDIA A100 40GB PCIe" in future responses).
        if g is None:
            for cand in gpus:
                if f["key"].split()[0] in (cand["name"] or "") and \
                        str(f["min_vram_gb"]) in (cand["name"] or ""):
                    g = cand
                    break
        if g is None:
            entry["status"] = "not_listed"
            entry["available"] = None
            entry["nodes"] = None
            entry["note"] = ("In /markets but NOT in /reservations/gpus: "
                             "availability unknown, do not assume free hosts.")
            warnings.append(
                f"{f['key']}: NOT LISTED in availability data — "
                "do not deploy assuming free hosts.")
        else:
            entry["status"] = ("available" if (g["available"] or 0) > 0
                               else "no_free_hosts")
            entry["available"] = g["available"]
            entry["nodes"] = g["nodes"]
            entry["working"] = g["working"]
            entry["price_min_usd_per_hour"] = g["price_min_usd_per_hour"]
            entry["price_max_usd_per_hour"] = g["price_max_usd_per_hour"]
            if (g["available"] or 0) == 0:
                entry["note"] = "0 free hosts right now — do not deploy."
                warnings.append(f"{f['key']}: 0 free hosts — DO NOT DEPLOY.")
            elif (g["available"] or 0) <= 2:
                entry["note"] = "Only 1–2 free hosts — deploy promptly."
                warnings.append(f"{f['key']}: only {g['available']} free host(s).")
        # Cheapest PREMIUM market for this class (credit-safe).
        premium = [m for m in markets
                   if m["slug"] == f["market_slug_prefix"]
                   and m["type"] == "PREMIUM"
                   and isinstance(m["usd_per_hour"], (int, float))]
        # Exclude the anomalous "reserved" market: VRAM 999GB is not a
        # real card class and its $0.10 price is not generally usable.
        premium = [m for m in premium if (m["vram_gb"] or 0) < 200]
        if premium:
            cheapest = min(premium, key=lambda m: m["usd_per_hour"])
            entry["cheapest_premium_market"] = {
                "slug": cheapest["slug"],
                "address": cheapest["address"],
                "usd_per_hour": cheapest["usd_per_hour"],
                "cached_ollama": cheapest["cached_ollama"],
                "cached_vllm": cheapest["cached_vllm"],
            }
        else:
            entry["cheapest_premium_market"] = None
        focus.append(entry)

    # Cheapest PREMIUM market meeting a minimum VRAM (credit-safe pick).
    def cheapest_premium(min_vram: int) -> dict | None:
        cands = [m for m in markets
                 if m["type"] == "PREMIUM"
                 and isinstance(m["usd_per_hour"], (int, float))
                 and isinstance(m["vram_gb"], int)
                 and 0 < m["vram_gb"] < 200
                 and m["vram_gb"] >= min_vram]
        if not cands:
            return None
        best = min(cands, key=lambda m: m["usd_per_hour"])
        return {"slug": best["slug"], "address": best["address"],
                "usd_per_hour": best["usd_per_hour"],
                "vram_gb": best["vram_gb"]}

    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "nosana-market-scout",
        "generated_at_utc": now_iso(),
        "sources": source_status,
        "focus": focus,
        "markets": markets,
        "gpus": gpus,
        "cheapest_premium_by_vram": {
            "12": cheapest_premium(12),
            "24": cheapest_premium(24),
            "32": cheapest_premium(32),
        },
        "warnings": warnings,
        "notes": [
            "/markets `nodes` is always an empty list; availability "
            "comes only from /reservations/gpus.",
            "COMMUNITY markets are excluded from cheapest-PREMIUM picks: "
            "credits only work on PREMIUM markets.",
            "A GPU class absent from /reservations/gpus has UNKNOWN "
            "availability, not zero availability.",
        ],
    }


def validate_snapshot(snap: dict) -> list[str]:
    """Schema validation. Returns a list of errors (empty = valid)."""
    errors: list[str] = []
    if not isinstance(snap, dict):
        return ["snapshot is not a JSON object"]
    if snap.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION} "
                      f"(got {snap.get('schema_version')!r})")
    ts = snap.get("generated_at_utc")
    if not isinstance(ts, str):
        errors.append("generated_at_utc must be an ISO-8601 string")
    else:
        try:
            datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            errors.append(f"generated_at_utc not parseable: {ts!r}")
    for key in ("focus", "markets", "gpus", "warnings"):
        if not isinstance(snap.get(key), list):
            errors.append(f"{key} must be a list")
    if isinstance(snap.get("markets"), list) and not snap["markets"]:
        errors.append("markets must not be empty")
    if isinstance(snap.get("gpus"), list) and not snap["gpus"]:
        errors.append("gpus must not be empty")
    if isinstance(snap.get("focus"), list):
        for entry in snap["focus"]:
            if not isinstance(entry, dict) or "gpu" not in entry \
                    or "status" not in entry:
                errors.append(f"focus entry missing gpu/status: {entry!r}")
                continue
            if entry["status"] not in ("available", "no_free_hosts",
                                       "not_listed"):
                errors.append(f"focus {entry['gpu']}: unknown status "
                              f"{entry['status']!r}")
            if entry["status"] == "available" and not isinstance(
                    entry.get("available"), int):
                errors.append(f"focus {entry['gpu']}: available must be "
                              "an int when status is available")
    if isinstance(snap.get("cheapest_premium_by_vram"), dict):
        for k, v in snap["cheapest_premium_by_vram"].items():
            if v is not None and not isinstance(v.get("usd_per_hour"),
                                                (int, float)):
                errors.append(f"cheapest_premium_by_vram[{k}] has no price")
    return errors


def print_summary(snap: dict) -> None:
    print(f"snapshot: {snap['generated_at_utc']} "
          f"(schema v{snap['schema_version']})")
    print(f"markets: {len(snap['markets'])}, "
          f"gpus: {len(snap['gpus'])}")
    for entry in snap["focus"]:
        if entry["status"] == "available":
            print(f"  {entry['gpu']:10} {entry['available']:3} free / "
                  f"{entry['nodes']} nodes  "
                  f"${entry['price_min_usd_per_hour']}"
                  f"–${entry['price_max_usd_per_hour']}/h")
        elif entry["status"] == "no_free_hosts":
            print(f"  {entry['gpu']:10}   0 free / {entry['nodes']} nodes  "
                  "DO NOT DEPLOY")
        else:
            print(f"  {entry['gpu']:10}  not listed in availability data")
    for w in snap["warnings"]:
        print(f"  WARNING: {w}")
    picks = snap["cheapest_premium_by_vram"]
    for vram in ("12", "24", "32"):
        pick = picks.get(vram)
        if pick:
            print(f"  cheapest PREMIUM >= {vram}GB VRAM: {pick['slug']} "
                  f"${pick['usd_per_hour']}/h")


def cmd_once(out_dir: Path) -> int:
    markets_raw, m_status, m_err = fetch_json(MARKETS_URL)
    gpus_raw, g_status, g_err = fetch_json(GPUS_URL)
    sources = {
        "markets": {"url": MARKETS_URL, "http_status": m_status,
                    "error": m_err},
        "gpus": {"url": GPUS_URL, "http_status": g_status, "error": g_err},
    }
    if markets_raw is None or gpus_raw is None:
        print("scout: FAILED to fetch live data")
        for err in (m_err, g_err):
            if err:
                print(f"  - {err}")
        print("No snapshot written. Check network / Nosana API status.")
        return 1
    if not isinstance(markets_raw, list) or not isinstance(gpus_raw, dict):
        print("scout: FAILED — unexpected response shape from Nosana API")
        return 1
    snap = build_snapshot(markets_raw, gpus_raw, sources)
    errors = validate_snapshot(snap)
    if errors:
        print("scout: snapshot failed validation, not written")
        for e in errors:
            print(f"  - {e}")
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = snap["generated_at_utc"].replace(":", "").replace("-", "")
    path = out_dir / f"{stamp}.json"
    latest = out_dir / "latest.json"
    path.write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n")
    latest.write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n")
    print_summary(snap)
    print(f"snapshot written: {path}")
    print(f"latest snapshot:  {latest}")
    return 0


def cmd_check(path: Path) -> int:
    if not path.is_file():
        print(f"check: file not found: {path}")
        return 1
    try:
        snap = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        print(f"check: INVALID JSON — {exc}")
        return 1
    errors = validate_snapshot(snap)
    if errors:
        print(f"check: FAILED ({path})")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"check: PASSED ({path})")
    print_summary(snap)
    return 0


def cmd_dry_run() -> int:
    print("scout --dry-run: no network, embedded SAMPLE data only")
    sources = {
        "markets": {"url": "sample (embedded)", "http_status": None,
                    "error": None},
        "gpus": {"url": "sample (embedded)", "http_status": None,
                 "error": None},
    }
    snap = build_snapshot(SAMPLE_MARKETS, SAMPLE_GPUS, sources)
    snap["generated_at_utc"] = "SAMPLE-NOT-LIVE"
    # --dry-run sample timestamp is not ISO; validate the rest manually.
    errors = [e for e in validate_snapshot(snap)
              if "generated_at_utc" not in e]
    if errors:
        print("dry-run: FAILED")
        for e in errors:
            print(f"  - {e}")
        return 1
    print_summary(snap)
    print("dry-run: PASSED (sample data, not live)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nosana Market Scout")
    parser.add_argument("--once", action="store_true",
                        help="Fetch live data and write a snapshot")
    parser.add_argument("--check", action="store_true",
                        help="Validate a snapshot file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate parsing against embedded sample data")
    parser.add_argument("--file", type=str, default=None,
                        help="Snapshot file for --check "
                             "(default: snapshots/latest.json)")
    parser.add_argument("--out-dir", type=str, default=None,
                        help="Snapshot output dir for --once "
                             "(default: snapshots/)")
    args = parser.parse_args(argv)

    if args.check:
        path = Path(args.file) if args.file else HERE / "snapshots" / "latest.json"
        return cmd_check(path)
    if args.dry_run:
        return cmd_dry_run()
    if args.once:
        out_dir = Path(args.out_dir) if args.out_dir else HERE / "snapshots"
        return cmd_once(out_dir)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
