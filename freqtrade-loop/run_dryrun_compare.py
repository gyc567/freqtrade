"""freqtrade-loop: Dry-run comparison report.

For each WF window, run dry-run replay and compare against the frozen-params
WF baseline (from wf_cycle14_phase1B_frozen_params.csv). Produces a side-by-side
table so we can see if dry-run results diverge from backtest.

Usage:
    python3 freqtrade-loop/run_dryrun_compare.py
    python3 freqtrade-loop/run_dryrun_compare.py --window=WF5
"""

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path("/Users/jie/code/freqtrade")
LOOP_DIR = ROOT / "freqtrade-loop"
STRATEGIES = ["TrendRider4h", "NostalgiaForInfinity"]
FROZEN_CSV = LOOP_DIR / "wf_cycle14_phase1B_frozen_params.csv"
DRYRUN_LOG_DIR = Path("/tmp/c4_results/dryrun")  # noqa: S108
REPORT_DIR = DRYRUN_LOG_DIR / "reports"
DRYRUN_SCRIPT = LOOP_DIR / "run_dryrun.py"

# Mirror windows from run_wf_phase1.py
WINDOWS = {
    "WF1": "20231007-20240207",
    "WF2": "20240307-20240707",
    "WF3": "20240807-20241207",
    "WF4": "20250107-20250507",
    "WF5": "20250607-20251007",
    "BLIND": "20260101-20260731",
    "DRY12MO": "20250801-20260731",
    "DRY90D": "20260501-20260731",
}


def _load_frozen_baseline() -> dict:
    """Load frozen-params WF results keyed by (window, strategy)."""
    baseline: dict = {}
    if not FROZEN_CSV.exists():
        return baseline
    with FROZEN_CSV.open() as f:
        for row in csv.DictReader(f):
            key = (row["window_id"], row["strategy"])
            avg_w = float(row.get("avg_winner_usdt") or 0)
            avg_l = float(row.get("avg_loser_usdt") or 0)
            rr = (avg_w / avg_l) if avg_l > 0 else float("inf")
            baseline[key] = {
                "trades": int(row.get("trades") or 0),
                "wins": int(row.get("wins") or 0),
                "losses": int(row.get("losses") or 0),
                "profit_total": float(row.get("profit_total_abs") or 0),
                "max_dd": float(row.get("max_drawdown_abs") or 0),
                "win_rate": float(row.get("win_rate") or 0),
                "real_rr": rr if rr != float("inf") else "inf",
                "source": "frozen_params_backtest",
            }
    return baseline


def _run_dryrun_for_window(strategy: str, timerange: str) -> dict:
    """Invoke run_dryrun.py for one (strategy, timerange). Returns summary dict."""
    print(f"  -> {strategy} {timerange} ...", end=" ", flush=True)
    proc = subprocess.run(
        [
            "ft_venv/bin/python3",
            str(DRYRUN_SCRIPT),
            f"--strategy={strategy}",
            f"--timerange={timerange}",
            "--no-prepare",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )
    if proc.returncode != 0:
        print(f"FAILED (exit {proc.returncode})")
        return {"status": "failed", "stderr": proc.stderr[-500:]}
    # Parse stdout to extract metrics
    out = proc.stdout
    summary: dict = {"status": "success"}
    for line in out.splitlines():
        line = line.strip()
        if "trades:" in line and "wins/losses:" in line:
            # Example: "  trades:      4"
            pass
    # Easier: parse the jsonl file just created
    jsonls = sorted(DRYRUN_LOG_DIR.glob(f"{strategy}_*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not jsonls:
        print("FAILED (no jsonl output)")
        return {"status": "failed", "reason": "no_output"}
    latest = jsonls[-1]
    summary["jsonl"] = str(latest)
    summary["timerange"] = timerange
    summary["strategy"] = strategy

    # Read back summary from the jsonl by counting events
    opens = closes = ticks = 0
    with latest.open() as f:
        for line in f:
            try:
                ev = json.loads(line)
                k = ev.get("kind")
                if k == "OPEN":
                    opens += 1
                elif k == "CLOSE":
                    closes += 1
                elif k == "TICK":
                    ticks += 1
            except json.JSONDecodeError:
                continue
    summary["n_open_events"] = opens
    summary["n_close_events"] = closes
    summary["n_ticks"] = ticks
    print(f"opens={opens} closes={closes}")
    return summary


def _parse_trade_csv(strategy: str, timerange: str) -> dict:
    """Read trade CSV from latest dryrun run."""
    csvs = sorted(DRYRUN_LOG_DIR.glob(f"{strategy}_*.csv"), key=lambda p: p.stat().st_mtime)
    if not csvs:
        return {"timerange": timerange, "trades": 0}
    latest = csvs[-1]
    with latest.open() as f:
        rows = list(csv.DictReader(f))
    if not rows or "trade_id" not in rows[0]:
        return {"timerange": timerange, "trades": 0}
    profits = [float(r.get("profit_abs") or 0) for r in rows]
    wins = sum(1 for p in profits if p > 0)
    losses = sum(1 for p in profits if p < 0)
    total = sum(profits)
    avg_w = sum(p for p in profits if p > 0) / max(wins, 1)
    avg_l = abs(sum(p for p in profits if p < 0)) / max(losses, 1)
    rr = (avg_w / avg_l) if avg_l > 0 else float("inf")
    return {
        "timerange": timerange,
        "csv": str(latest),
        "trades": len(rows),
        "wins": wins,
        "losses": losses,
        "profit_total": total,
        "win_rate": wins / max(len(rows), 1),
        "avg_winner": round(avg_w, 2),
        "avg_loser": round(avg_l, 2),
        "real_rr": round(rr, 2) if rr != float("inf") else "inf",
        "exit_reasons": _count_exit_reasons(rows),
    }


def _count_exit_reasons(rows: list[dict]) -> dict:
    counts: dict = {}
    for r in rows:
        reason = r.get("exit_reason") or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _compare(baseline: dict, dryrun: dict, window: str, strategy: str) -> dict:
    """Compute deltas between baseline and dryrun."""
    if not baseline:
        return {
            "window": window,
            "strategy": strategy,
            "baseline": baseline,
            "dryrun": dryrun,
            "verdict": "no_baseline (window not in frozen-params WF)",
        }
    if not dryrun or dryrun.get("trades", 0) == 0:
        return {
            "window": window,
            "strategy": strategy,
            "baseline": baseline,
            "dryrun": dryrun,
            "verdict": "no_trades (regime-incompatible or signal-absent)",
        }
    wr_dev = (dryrun["win_rate"] - baseline["win_rate"]) * 100
    pnl_dev = dryrun["profit_total"] - baseline["profit_total"]
    return {
        "window": window,
        "strategy": strategy,
        "baseline": baseline,
        "dryrun": dryrun,
        "wr_dev_pct": round(wr_dev, 1),
        "pnl_dev_usdt": round(pnl_dev, 2),
        "verdict": _verdict(wr_dev, pnl_dev),
    }


def _verdict(wr_dev: float, pnl_dev: float) -> str:
    if abs(wr_dev) <= 15 and abs(pnl_dev) <= 30:
        return "PASS"
    if abs(wr_dev) <= 25 and abs(pnl_dev) <= 50:
        return "MARGINAL"
    return "FAIL"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", help="Limit to one window (e.g., WF5, BLIND, DRY90D)")
    parser.add_argument(
        "--strategy",
        choices=STRATEGIES,
        help="Limit to one strategy",
    )
    args = parser.parse_args()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    baseline = _load_frozen_baseline()
    print(f"Loaded {len(baseline)} frozen-params baselines")

    windows = (
        {args.window: WINDOWS[args.window]}
        if args.window
        else {k: v for k, v in WINDOWS.items() if k not in ("BLIND",)}
    )
    strategies = [args.strategy] if args.strategy else STRATEGIES

    print(f"Windows: {list(windows.keys())}")
    print(f"Strategies: {strategies}")
    print()

    rows = []
    for wid, tr in windows.items():
        for strat in strategies:
            print(f"[{wid} x {strat}]")
            # Run dry-run replay
            _run_dryrun_for_window(strat, tr)
            # Parse the trade CSV
            dryrun_metrics = _parse_trade_csv(strat, tr)
            # Compare against frozen baseline (use BLIND if window == BLIND for baseline lookup)
            base_key = (wid, strat)
            base = baseline.get(base_key, {})
            comp = _compare(base, dryrun_metrics, wid, strat)
            rows.append(comp)
            d_trades = dryrun_metrics.get("trades", 0)
            d_wr = dryrun_metrics.get("win_rate", 0) * 100
            d_pnl = dryrun_metrics.get("profit_total", 0)
            b_trades = base.get("trades", 0)
            b_wr = base.get("win_rate", 0) * 100
            b_pnl = base.get("profit_total", 0)
            print(f"  dryrun:   trades={d_trades}, WR={d_wr:.0f}%, P/L={d_pnl:.2f}")
            print(f"  baseline: trades={b_trades}, WR={b_wr:.0f}%, P/L={b_pnl:.2f}")
            print(f"  verdict: {comp.get('verdict')}")
            print()

    # Write report
    _write_report(rows)
    print(f"Report: {REPORT_DIR / 'comparison_report.md'}")


def _write_report(rows: list[dict]) -> None:
    md: list[str] = []
    md.append("# Cycle 14 Phase 4 — Dry-Run Comparison Report")
    md.append("")
    md.append("Dry-run replay vs frozen-params backtest baseline.")
    md.append("PASS = within ±15% WR and ±30 USDT P/L.")
    md.append("")
    md.append(
        "| Window | Strategy | BaseTr | RunTr | BaseWR | RunWR | "
        "WR dev | BasePnL | RunPnL | PnL dev | Verdict |"
    )
    md.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        b = r.get("baseline", {})
        d = r.get("dryrun", {})
        wr_dev = r.get("wr_dev_pct", "-") if "wr_dev_pct" in r else "-"
        pnl_dev = r.get("pnl_dev_usdt", "-") if "pnl_dev_usdt" in r else "-"
        md.append(
            f"| {r['window']} | {r['strategy']} | "
            f"{b.get('trades', '-')} | {d.get('trades', '-')} | "
            f"{b.get('win_rate', 0) * 100:.0f}% | {d.get('win_rate', 0) * 100:.0f}% | "
            f"{wr_dev}% | "
            f"{b.get('profit_total', 0):.2f} | {d.get('profit_total', 0):.2f} | "
            f"{pnl_dev} | "
            f"{r.get('verdict', '-')} |"
        )
    md.append("")
    md.append("## Notes")
    md.append("")
    md.append("- Dry-run uses freqtrade's Backtesting class with frozen buy_params from .py")
    md.append("- Baseline is the frozen-params WF (Cycle 14 Phase 1B)")
    md.append("- 90D window is BTC recovery from $58k crash (regime-incompatible)")
    (REPORT_DIR / "comparison_report.md").write_text("\n".join(md) + "\n")


if __name__ == "__main__":
    sys.exit(main() or 0)
