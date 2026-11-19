# plot_results.py - Member C: raw experiment files -> results/graphs/*.png, results/*.csv and
# results/summary.md (skips experiments whose raw files are missing)
#   ~/nrrs-env/bin/python evaluation/plot_results.py

import json
import os
import re

import pandas as pd
import matplotlib
matplotlib.use("Agg")    # no screen needed
import matplotlib.pyplot as plt

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(PROJECT_DIR, "results")
RAW = os.path.join(RESULTS, "raw")
GRAPHS = os.path.join(RESULTS, "graphs")

RES_COLOR = "#2b6cb0"     # reservation mode
BASE_COLOR = "#c05621"    # baseline
CORE_LINK = re.compile(r"^s\d+->s\d+$")      # switch-to-switch links only

summary_lines = []


def raw(name):
    return os.path.join(RAW, name)


def have(*names):
    missing = [n for n in names if not os.path.exists(raw(n))]
    if missing:
        print("skip (missing %s)" % ", ".join(missing))
    return not missing


def save(fig, name):
    fig.tight_layout()
    fig.savefig(os.path.join(GRAPHS, name), dpi=150)
    plt.close(fig)
    print("saved results/graphs/" + name)


def note(text):
    summary_lines.append(text)


def core_util(util_file):
    df = pd.read_csv(util_file)
    return df[df["link"].str.match(CORE_LINK)]


# E1
def e1():
    print("E1:", end=" ")
    if not have("e1_requests_reservation.csv", "e1_requests_baseline.csv"):
        return
    rows = []
    for mode in ("reservation", "baseline"):
        req = pd.read_csv(raw("e1_requests_%s.csv" % mode))
        rounds = pd.read_csv(raw("e1_rounds_%s.csv" % mode))
        util = core_util(raw("util_%s.csv" % mode))
        for _, r in rounds.iterrows():
            part = req[req["users"] == r["users"]]
            window = util[(util["timestamp"] >= r["start"]) & (util["timestamp"] <= r["end"])]
            per_tick = window.groupby("timestamp")["utilization"].max()
            rows.append({
                "mode": mode, "users": r["users"], "requests": len(part),
                "accepted": int((part["result"] == "ACCEPT").sum()),
                "acceptance_ratio": round((part["result"] == "ACCEPT").mean(), 3),
                "no_capacity": int((part["reason"] == "NO_CAPACITY").sum()),
                "conflict": int((part["reason"] == "CONFLICT").sum()),
                "other_reject": int(((part["result"] != "ACCEPT") & ~part["reason"].isin(["NO_CAPACITY", "CONFLICT"])).sum()),
                "avg_reply_ms": round(part.loc[part["result"] == "ACCEPT", "reply_ms"].mean(), 2),
                "peak_booked_util": round(per_tick.max(), 3) if len(per_tick) else 0,
                "time_overbooked_pct": round(100 * (per_tick > 1.0).mean(), 1) if len(per_tick) else 0,
            })
    table = pd.DataFrame(rows)
    table.to_csv(os.path.join(RESULTS, "e1_admission.csv"), index=False)
    res, base = table[table["mode"] == "reservation"], table[table["mode"] == "baseline"]

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(res["users"], res["acceptance_ratio"], "o-", color=RES_COLOR, label="With reservation")
    ax.plot(base["users"], base["acceptance_ratio"], "s--", color=BASE_COLOR, label="Baseline (accept all)")
    ax.set_xlabel("Concurrent users"); ax.set_ylabel("Acceptance ratio"); ax.set_ylim(0, 1.05)
    ax.set_title("E1: Acceptance ratio under load"); ax.legend(); ax.grid(alpha=0.3)
    save(fig, "e1_acceptance_ratio.png")

    fig, ax = plt.subplots(figsize=(6, 4))
    x = range(len(res)); w = 0.35
    ax.bar([i - w / 2 for i in x], res["peak_booked_util"] * 100, w, color=RES_COLOR, label="With reservation")
    ax.bar([i + w / 2 for i in x], base["peak_booked_util"].values * 100, w, color=BASE_COLOR, label="Baseline")
    ax.axhline(100, color="black", linestyle=":", label="Link capacity")
    ax.set_xticks(list(x)); ax.set_xticklabels(res["users"])
    ax.set_xlabel("Concurrent users"); ax.set_ylabel("Peak booked utilization of a core link (%)")
    ax.set_title("E1: Bandwidth promised vs link capacity"); ax.legend()
    save(fig, "e1_peak_utilization.png")

    fig, ax = plt.subplots(figsize=(6, 4))
    labels = res["users"].astype(str)
    ax.bar(labels, res["accepted"], color="#2f855a", label="Accepted")
    ax.bar(labels, res["no_capacity"], bottom=res["accepted"], color="#9b2c2c", label="NO_CAPACITY")
    ax.bar(labels, res["conflict"], bottom=res["accepted"] + res["no_capacity"], color="#d69e2e", label="CONFLICT")
    ax.set_xlabel("Concurrent users"); ax.set_ylabel("Requests")
    ax.set_title("E1: What happened to the requests (reservation mode)"); ax.legend()
    save(fig, "e1_request_outcomes.png")

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(res["users"], res["avg_reply_ms"], "o-", color=RES_COLOR, label="With reservation")
    ax.plot(base["users"], base["avg_reply_ms"], "s--", color=BASE_COLOR, label="Baseline")
    ax.set_xlabel("Concurrent users"); ax.set_ylabel("Average setup time (ms)")
    ax.set_title("E1: Request -> rules installed -> ACCEPT"); ax.legend(); ax.grid(alpha=0.3)
    save(fig, "e1_setup_time.png")

    note("## E1 Admission under load\n")
    note(table.to_markdown(index=False) if hasattr(table, "to_markdown") else table.to_string(index=False))
    note("")


# E2
def parse_ping(path):
    times, rtts = [], []
    start = None
    for line in open(path):
        m = re.search(r"\[([\d.]+)\].*time=([\d.]+) ms", line)
        if m:
            t = float(m.group(1))
            start = start or t
            times.append(t - start)
            rtts.append(float(m.group(2)))
    return times, rtts


def e2():
    print("E2:", end=" ")
    if not have("e2_throughput_reservation.csv", "e2_throughput_baseline.csv"):
        return
    fig, ax = plt.subplots(figsize=(7, 4))
    rows = []
    for mode, color in (("reservation", RES_COLOR), ("baseline", BASE_COLOR)):
        df = pd.read_csv(raw("e2_throughput_%s.csv" % mode))
        ax.plot(df["time_s"], df["mbps"], "o-", ms=3, color=color, label="h1->h2, %s" % mode)
        flood = df[df["flood_on"] == 1]
        quiet = df[(df["flood_on"] == 0) & (df["time_s"] > 2)]
        row = {"mode": mode, "avg_mbps_no_flood": round(quiet["mbps"].mean(), 2),
               "avg_mbps_during_flood": round(flood["mbps"].mean(), 2),
               "min_mbps_during_flood": round(flood["mbps"].min(), 2)}
        if os.path.exists(raw("e2_ping_%s.txt" % mode)):
            t, rtt = parse_ping(raw("e2_ping_%s.txt" % mode))
            f0, f1 = flood["time_s"].min() - 1, flood["time_s"].max()
            during = [r for tt, r in zip(t, rtt) if f0 <= tt <= f1]
            row["avg_ping_ms_during_flood"] = round(sum(during) / len(during), 1) if during else None
            row["ping_lost_during_flood_pct"] = round(100 * (1 - len(during) / ((f1 - f0) / 0.2)), 1)
        rows.append(row)
    ax.axvspan(flood["time_s"].min() - 1, flood["time_s"].max(), color="grey", alpha=0.15, label="30 Mbps UDP flood h3->h4")
    ax.axhline(5, color="black", linestyle=":", label="Reserved: 5 Mbps")
    ax.set_xlabel("Time (s)"); ax.set_ylabel("TCP throughput (Mbps)")
    ax.set_title("E2: Reserved flow under congestion (Path A, 10 Mbps)"); ax.legend(fontsize=8); ax.grid(alpha=0.3)
    save(fig, "e2_guarantee_throughput.png")

    fig, ax = plt.subplots(figsize=(7, 4))
    for mode, color in (("reservation", RES_COLOR), ("baseline", BASE_COLOR)):
        if os.path.exists(raw("e2_ping_%s.txt" % mode)):
            t, rtt = parse_ping(raw("e2_ping_%s.txt" % mode))
            ax.plot(t, rtt, ".", ms=4, color=color, label=mode)
    ax.axvspan(flood["time_s"].min() - 1, flood["time_s"].max(), color="grey", alpha=0.15, label="UDP flood")
    ax.set_yscale("log"); ax.set_xlabel("Time (s)"); ax.set_ylabel("Ping h1->h2 (ms, log scale)")
    ax.set_title("E2: Delay of the reserved flow"); ax.legend(fontsize=8); ax.grid(alpha=0.3)
    save(fig, "e2_guarantee_delay.png")

    table = pd.DataFrame(rows)
    table.to_csv(os.path.join(RESULTS, "e2_congestion.csv"), index=False)
    note("## E2 Guarantee under congestion (5 Mbps reserved, 30 Mbps UDP flood)\n")
    note(table.to_markdown(index=False))
    note("")


# E3
def e3():
    print("E3:", end=" ")
    if not have("util_core10.csv", "util_core20.csv"):
        return
    fig, ax = plt.subplots(figsize=(7, 4))
    rows = []
    for label, color, style in (("core10", RES_COLOR, "-"), ("core20", "#2f855a", "-")):
        util = core_util(raw("util_%s.csv" % label))
        win = pd.read_csv(raw("e3_window_%s.csv" % label)).iloc[0]
        util = util[(util["timestamp"] >= win["start"]) & (util["timestamp"] <= win["end"])]
        per_tick = util.groupby("timestamp")["utilization"].agg(["mean", "max"])
        t = per_tick.index - per_tick.index.min()
        ax.plot(t, per_tick["mean"] * 100, style, color=color, label="%s Mbps links: average" % label[4:])
        ax.plot(t, per_tick["max"] * 100, "--", color=color, alpha=0.6, label="%s Mbps links: busiest link" % label[4:])
        req = pd.read_csv(raw("e3_requests_%s.csv" % label))
        rows.append({"core_link_mbps": int(label[4:]), "requests": len(req),
                     "acceptance_ratio": round((req["result"] == "ACCEPT").mean(), 3),
                     "no_capacity": int((req["reason"] == "NO_CAPACITY").sum()),
                     "avg_core_util_pct": round(100 * per_tick["mean"].mean(), 1),
                     "peak_core_util_pct": round(100 * per_tick["max"].max(), 1)})
    ax.axhline(100, color="black", linestyle=":")
    ax.set_xlabel("Time (s)"); ax.set_ylabel("Booked utilization of core links (%)")
    ax.set_title("E3: Same load (20 users), 10 vs 20 Mbps links"); ax.legend(fontsize=8); ax.grid(alpha=0.3)
    save(fig, "e3_utilization.png")
    table = pd.DataFrame(rows)
    table.to_csv(os.path.join(RESULTS, "e3_utilization.csv"), index=False)
    note("## E3 Utilization with different link capacities\n")
    note(table.to_markdown(index=False))
    note("")


# E4
def e4():
    print("E4:", end=" ")
    if not have("e4_timeline.csv", "e4_waves.csv"):
        return
    df = pd.read_csv(raw("e4_timeline.csv"))
    waves = pd.read_csv(raw("e4_waves.csv"))
    fig, ax1 = plt.subplots(figsize=(7, 4))
    ax1.plot(df["elapsed_s"], df["reserved_mbps"], color=RES_COLOR, label="Reserved Mbps (controller)")
    ax1.set_xlabel("Time (s)"); ax1.set_ylabel("Reserved Mbps")
    ax2 = ax1.twinx()
    ax2.step(df["elapsed_s"], df["reserved_rules_in_switches"], color="#2f855a", where="post",
             label="Reservation rules in the switches")
    ax2.set_ylabel("Rules (ovs-ofctl dump-flows)")
    for _, w in waves.iterrows():
        ax1.axvline(w["fired_at_s"], color="grey", linestyle=":")
        ax1.annotate("%d/%d" % (w["accepted"], w["accepted"] + w["rejected"]), (w["fired_at_s"], ax1.get_ylim()[1] * 0.92),
                     fontsize=8, color="grey", ha="left")
    ax1.set_title("E4: Expiry frees bandwidth and rules (dotted = 12 new 3 s requests, accepted/sent)")
    h1, l1 = ax1.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=2, fontsize=8)
    save(fig, "e4_expiry.png")
    waves.to_csv(os.path.join(RESULTS, "e4_expiry.csv"), index=False)
    last = df.iloc[-1]
    note("## E4 Expiry\n")
    note(waves.to_markdown(index=False))
    note("\nAt the end: %d active reservations, %d reservation rules in the switches -> %s\n" % (
        last["active_reservations"], last["reserved_rules_in_switches"],
        "PASS" if last["active_reservations"] == 0 and last["reserved_rules_in_switches"] == 0 else "FAIL"))


# E5
def e5():
    print("E5:", end=" ")
    if not have("e5_iperf.txt", "e5_link_failure.json"):
        return
    info = json.load(open(raw("e5_link_failure.json")))
    points = []
    for m in re.finditer(r"\]\s+([\d.]+)-([\d.]+)\s+sec\s+[\d.]+ \w?Bytes\s+([\d.]+) (\w)bits/sec", open(raw("e5_iperf.txt")).read()):
        start, end = float(m.group(1)), float(m.group(2))
        if end - start < 1.5:
            scale = {"K": 0.001, "M": 1, "G": 1000}.get(m.group(4), 1e-6)
            points.append((end, float(m.group(3)) * scale))
    fig, ax = plt.subplots(figsize=(7, 4))
    times = [p[0] for p in points]
    values = [p[1] for p in points]
    # 0.5 s samples jump between ~0 and ~6 because the meter drops TCP bursts,
    # so also draw a 1-second moving average to make the failure easy to see
    smooth = pd.Series(values).rolling(2, min_periods=1).mean()
    ax.plot(times, values, ".", ms=4, color=RES_COLOR, alpha=0.35, label="0.5 s samples")
    ax.plot(times, smooth, "-", lw=2, color=RES_COLOR, label="Reserved flow h1->h2 (1 s average)")
    ax.axhline(5, color="black", linestyle=":", label="Reserved: 5 Mbps")
    ax.axvline(info["fail_at_s"], color="red", linestyle="--", label="link s1-s2 down")
    if info.get("throughput_recovered_at_s"):
        ax.axvspan(info["fail_at_s"], info["throughput_recovered_at_s"], color="red", alpha=0.1,
                   label="recovery %.1f s" % info["recovery_time_s"])
    ax.set_xlabel("Time (s)"); ax.set_ylabel("Throughput (Mbps)"); ax.set_ylim(bottom=0)
    ax.set_title("E5: Link failure, reservation moved from Path A to Path B"); ax.legend(fontsize=8); ax.grid(alpha=0.3)
    save(fig, "e5_link_failure.png")
    pd.DataFrame([info]).to_csv(os.path.join(RESULTS, "e5_link_failure.csv"), index=False)
    note("## E5 Link failure\n")
    for k, v in info.items():
        note("- %s: %s" % (k, v))
    note("")


def scenarios():
    if os.path.exists(raw("scenarios.txt")):
        note("## Robustness scenarios (server)\n")
        note("```\n" + open(raw("scenarios.txt")).read().strip() + "\n```\n")


if __name__ == "__main__":
    os.makedirs(GRAPHS, exist_ok=True)
    e1(); e2(); e3(); e4(); e5(); scenarios()
    with open(os.path.join(RESULTS, "summary.md"), "w") as f:
        f.write("# Results summary\n\nGenerated by evaluation/plot_results.py from results/raw/.\n\n")
        f.write("\n".join(summary_lines) + "\n")
    print("saved results/summary.md")
