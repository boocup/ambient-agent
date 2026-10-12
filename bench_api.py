"""Time calls to the hosted Claude API, so Wi-Fi and Ethernet can be compared. Reads nothing from the rack.

Each run streams one short, fixed reply and records:
  ttft    seconds until the first text arrives (network + the server starting to answer)
  total   seconds for the whole reply
  tok/s   output tokens per second after the first text (mostly the model, not your network)
plus a no-key network check (connect, TLS, first byte to api.anthropic.com), repeated a few times.

Results are appended to bench_results/api_bench.jsonl with a label for the connection. The label comes from the default
network interface (Wi-Fi or Ethernet) unless you pass --label. Run it again after changing the connection, then compare:

    python bench_api.py                  # 10 timed calls on the current connection
    python bench_api.py --compare        # medians per label from every saved run

Each call is a few dozen input tokens and a few dozen output tokens, a small fraction of a cent. Needs ANTHROPIC_API_KEY
(the SDK reads it from the environment; it is never printed or saved).
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import time
from datetime import datetime
from pathlib import Path

import anthropic

DEFAULT_MODEL = "claude-sonnet-5-5"   # what this project and the coding sessions use
PROMPT = "Count from 1 to 40, separated by single spaces. Output only the numbers."
RESULTS = Path(__file__).parent / "bench_results" / "api_bench.jsonl"


def default_connection() -> tuple[str, str]:
    """('en1', 'Wi-Fi') for the interface macOS uses by default; falls back to ('?', 'unknown')."""
    try:
        route = subprocess.run(["route", "-n", "get", "default"], capture_output=True, text=True, timeout=5).stdout
        device = next(line.split(":")[1].strip() for line in route.splitlines() if "interface:" in line)
        ports = subprocess.run(["networksetup", "-listallhardwareports"], capture_output=True, text=True, timeout=5).stdout
        name = ""
        for line in ports.splitlines():
            if line.startswith("Hardware Port:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("Device:") and line.split(":", 1)[1].strip() == device:
                return device, name
        return device, "unknown"
    except Exception:
        return "?", "unknown"


def network_check(times: int = 5) -> dict:
    """Connect / TLS / first-byte times to the API host, with no key and no data (the page it returns is an error)."""
    rows = []
    for _ in range(times):
        out = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-m", "10", "-w", "%{time_connect} %{time_appconnect} %{time_starttransfer}",
             "https://api.anthropic.com/"], capture_output=True, text=True).stdout.split()
        if len(out) == 3:
            rows.append([float(x) for x in out])
    if not rows:
        return {}
    med = lambda i: round(statistics.median(r[i] for r in rows), 4)
    return {"connect": med(0), "tls": med(1), "first_byte": med(2), "n": len(rows)}


def one_call(client: anthropic.Anthropic, model: str) -> dict:
    """Stream one reply and time it. Thinking is turned off so every run does the same work."""
    kwargs = dict(model=model, max_tokens=200, messages=[{"role": "user", "content": PROMPT}])
    started = time.perf_counter()
    first = None
    for attempt in (0, 1):
        try:
            extra = {"thinking": {"type": "between_tools"}} if attempt == 0 else {}   # Sonnet 5.5's way to switch thinking off
            with client.messages.stream(**kwargs, **extra) as stream:
                for _ in stream.text_stream:
                    if first is None:
                        first = time.perf_counter()
                final = stream.get_final_message()
            break
        except anthropic.BadRequestError:
            if attempt == 1:
                raise
            started, first = time.perf_counter(), None      # that model refused the thinking setting; retry without it
    ended = time.perf_counter()
    out_tokens = final.usage.output_tokens
    ttft = (first or ended) - started
    return {"ttft": round(ttft, 3), "total": round(ended - started, 3), "out_tokens": out_tokens,
            "tok_s": round(out_tokens / max(ended - (first or started), 1e-6), 1), "request_id": getattr(final, "_request_id", None)}


def summarize(rows: list[dict], key: str) -> str:
    v = [r[key] for r in rows]
    return f"median {statistics.median(v):.3f}  min {min(v):.3f}  max {max(v):.3f}"


def compare() -> None:
    if not RESULTS.exists():
        raise SystemExit("No saved results yet. Run python bench_api.py first.")
    runs = [json.loads(line) for line in RESULTS.read_text().splitlines() if line.strip()]
    labels = sorted({r["label"] for r in runs})
    print(f"{'connection':<22}{'runs':>5}{'calls':>7}{'net connect':>13}{'net 1st byte':>14}{'ttft med':>10}{'total med':>11}{'tok/s med':>11}")
    for label in labels:
        group = [r for r in runs if r["label"] == label]
        calls = [c for r in group for c in r["calls"] if not c.get("cold")]
        net = [r["network"] for r in group if r.get("network")]
        print(f"{label:<22}{len(group):>5}{len(calls):>7}"
              f"{statistics.median(n['connect'] for n in net) * 1000 if net else 0:>11.0f}ms"
              f"{statistics.median(n['first_byte'] for n in net) * 1000 if net else 0:>12.0f}ms"
              f"{statistics.median(c['ttft'] for c in calls):>10.2f}"
              f"{statistics.median(c['total'] for c in calls):>11.2f}"
              f"{statistics.median(c['tok_s'] for c in calls):>11.1f}")
    print("(times in seconds unless marked ms; the first call of each run is excluded because it opens the connection)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--calls", type=int, default=10, help="timed calls (default 10; one extra cold call is made first)")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--label", help="name for this connection (default: detected, e.g. 'Wi-Fi (en1)')")
    p.add_argument("--compare", action="store_true", help="print medians per label from saved runs and exit")
    args = p.parse_args()
    if args.compare:
        return compare()
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise SystemExit("ANTHROPIC_API_KEY isn't set in this shell.")

    device, port = default_connection()
    label = args.label or f"{port} ({device})"
    print(f"Connection: {label} | model {args.model} | {args.calls} timed calls")
    net = network_check()
    if net:
        print(f"  network only (no key): connect {net['connect'] * 1000:.0f} ms, TLS {net['tls'] * 1000:.0f} ms, "
              f"first byte {net['first_byte'] * 1000:.0f} ms (median of {net['n']})")

    client = anthropic.Anthropic(max_retries=0, timeout=60)   # no hidden retries, so slow calls show up as slow
    calls = []
    for i in range(args.calls + 1):
        r = one_call(client, args.model)
        r["cold"] = (i == 0)
        calls.append(r)
        print(f"  call {i}{' (cold, not counted)' if i == 0 else ''}: first text {r['ttft']:.2f} s, total {r['total']:.2f} s, "
              f"{r['out_tokens']} tokens, {r['tok_s']} tok/s")
    timed = [c for c in calls if not c["cold"]]
    print(f"first text  {summarize(timed, 'ttft')}")
    print(f"total       {summarize(timed, 'total')}")
    print(f"tokens/s    {summarize(timed, 'tok_s')}")

    RESULTS.parent.mkdir(exist_ok=True)
    with RESULTS.open("a") as f:
        f.write(json.dumps({"when": datetime.now().isoformat(timespec="seconds"), "label": label, "model": args.model,
                            "network": net, "calls": calls}) + "\n")
    print(f"Saved to {RESULTS.relative_to(Path(__file__).parent)}. Compare connections with: python bench_api.py --compare")


if __name__ == "__main__":
    main()
