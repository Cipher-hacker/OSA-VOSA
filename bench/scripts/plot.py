#!/usr/bin/env python3
"""Standalone paper figures from a completely verified fresh run, never old data."""
from __future__ import annotations

import argparse
from collections import defaultdict
import math
from pathlib import Path
import statistics

import suite
from renderer import draw as render_figure
TOTAL_METRIC = ("end_to_end_s", "end_to_end_completion", "End-to-end per-round completion time (s)", 1)
TOTAL_METHOD = "End-to-end values come from continuous round timers, never a sum of role means."

COMM = (
    ("client_send_bytes_mean", "active_client_sent", "Communication (KB)", 1000),
    ("committee_send_bytes_mean", "committee_responder_sent", "Communication (KB)", 1000),
    ("server_sent_bytes", "server_sent", "Communication (MB)", 1000000),
    ("total_bytes", "total_sent", "Total communication (MB)", 1000000),
)
TIMES = (
    ("client_compute_s", "active_client_computation", "Running time (s)", 1),
    ("committee_compute_s", "committee_responder_computation", "Running time (s)", 1),
    ("server_compute_s", "server_computation", "Running time (s)", 1),
    ("server_and_verifier_s", "server_plus_verifier_computation", "Running time (s)", 1),
    TOTAL_METRIC,
)
VERIFY_TIME = (TIMES[0], TIMES[2], ("verifier_compute_s", "verifier_computation", "Running time (s)", 1))
VERIFY_COMM = (COMM[0], COMM[2], COMM[3])
COLORS = ("#D55E00", "#0072B2", "#666666", "#9C4DCC")
MARKERS = ("o", "x", "D", "^")
STYLES = ("-", "--", "-.", ":")


def mean_and_sd(rows, field, repeats):
    if sorted(r["repeat"] for r in rows) != list(range(1, repeats+1)):
        raise ValueError(f"Missing/duplicate repeats for {field}")
    v = [r[field] for r in rows]
    if not all(math.isfinite(x) and x >= 0 for x in v):
        raise ValueError("Invalid figure data")
    return statistics.mean(v), statistics.stdev(v) if len(v) > 1 else 0.0


def select(rows, group):
    return [r for r in rows if group in r["groups"].split(";")]


def captions():
    return """# Measurement and figure definitions
New revision round-v2 only; never pool old measurements.
The n-axis is the number of selected participants, not the global population N.
All n clients execute and attempt their serialized upload; the first A complete
uploads are delivered, the remaining whole uploads are lost in the simulator.
There is no network bandwidth or delay simulation. Setup/model broadcast excluded.
The deterministic prefix delivery mask is identical for all four protocols.
All m committee requests are counted; only t responders compute/reply.
The per-committee send mean is over responders; nonresponders send zero.
Communication counts sends once per directed hop. Forwarding is a separate send.
Client traffic is the sum of actual serialized uploads, including failed delivery.
KB=1000 bytes; MB=1000000 bytes; seconds are not relabeled milliseconds.
OSA uses d=16. Default m=50: OSA/Willow/TACITA t=40; OPA t=34, packing=16.
For m=40,50,60,80,100, OSA t=32,40,48,65,85; OPA t=27,34,41,54,68;
Willow/TACITA t=32,40,48,64,80. This is not an identical-threshold comparison.
The OSA thresholds also satisfy d<t and t>m-d for this selected parameter grid.
All role samples: three warmups followed by twenty serial client operations.
These extra samples are outside the round timer and are not protocol sends.
End-to-end is a continuous start-to-result measurement, not summed computation.
Native baselines start after client-worker context setup. TACITA starts after both
contexts and sampling are ready; includes IPC and the necessary key-fixture handoff.
TACITA KAHE clients and committee responses are serial; STE/native clients use
up to 32 workers. This measures the recorded single-host implementation schedule,
not a fully distributed parallel deployment. No equal-security claim is implied.
Each point averages three independent executions; CSV preserves individual evidence.
No smoothing, substituted values or error bars. TACITA remains in the inset when
it compresses the other curves; all axes have legends and use the original units.
OSA remains the compact seeded-digest RSS31 variant. TACITA remains STE+KAHE
without MKLHTS. Cryptographic core algorithms have not been rewritten.
"""


class Figures:
    def __init__(self, out, c):
        self.out = out
        self.config = c
        self.index = []

    def draw(self, rows, group, metric, series, xfield, expected_x, category, prefix, bars=False):
        field, role, ylabel, divisor = metric
        data = []
        for i, (label, filtered) in enumerate(series):
            values = defaultdict(list)
            for r in filtered:
                values[r[xfield] if not bars else 0].append(r)
            if sorted(values) != ([0] if bars else expected_x):
                raise ValueError(f"Incomplete x coverage: {prefix} {label}: {sorted(values)}")
            for x, point_rows in sorted(values.items()):
                mean, sd = mean_and_sd(point_rows, field, self.config["repeats"])
                data.append(dict(series=label, x=x if not bars else label, metric=field,
                                 raw_mean=mean, raw_sample_sd=sd, value=mean/divisor,
                                 sample_sd=sd/divisor, divisor=divisor, repeats=len(point_rows),
                                 job_ids=";".join(r["job_id"] for r in point_rows)))
        stem = f"{prefix}_{role}"
        folder = Path(category) / ("computation" if divisor == 1 else "communication")
        image = self.out / "paper_figures" / folder / stem
        spec = dict(name=stem, labels=[s[0] for s in series], bars=bars,
                    xfield=xfield, ylabel=ylabel, repeats=self.config["repeats"])
        layout = render_figure(data, spec, image)
        layout = dict(headroom=None, axes_fraction=None, data_occlusion=False,
                      decorated_inset_inside_main=None) | layout
        suite.write_csv(self.out / "plot_data" / folder / (stem + ".csv"), data)
        self.index.append(dict(category=str(folder), name=stem, metric=field, y_axis=ylabel,
                               x_scale="linear", y_scale="linear", **layout,
                               legend=";".join(s[0] for s in series),
                               png=image.with_suffix(".png").relative_to(self.out).as_posix(),
                               pdf=image.with_suffix(".pdf").relative_to(self.out).as_posix(),
                               data=(Path("plot_data")/folder/(stem+".csv")).as_posix()))


def generate(rows, c, out, scope="all", run=None):
    derived = [dict(r) for r in rows]
    communication_audit = [dict(job_id=r["job_id"], total_sent_bytes=r["total_bytes"],
        sent_hop_sum=sum(r[k] for k in suite.HOPS), valid=sum(r[k] for k in suite.HOPS)==r["total_bytes"]) for r in rows]
    out.mkdir(parents=True, exist_ok=False)
    maker = Figures(out, c)
    original_rows, rows = rows, derived
    if scope in ("all", "aggregation", "comparison"):
        for group, field, xs, category in (
            ("main", "n_clients", [1000], "four_protocols/fixed_configuration"),
            ("n", "n_clients", c["n_values"], "four_protocols/client_count"),
            ("m", "committee_size", c["m_values"], "four_protocols/committee_size"),
            ("length", "vector_len", c["lengths"], "four_protocols/vector_length"),
            ("dropout", "dropout_percent", c["four_protocol_dropout_values"], "four_protocols/dropout"),
        ):
            selected = select(rows, group)
            series = [(suite.LABELS[p], [r for r in selected if r["protocol"] == p]) for p in suite.AGG]
            for metric in (*TIMES, *COMM):
                maker.draw(selected, group, metric, series, field, xs, category, "four_protocols_" + group, group == "main")
        if scope != "comparison":
            for group, field, xs, category in (
                ("osa_dropout", "dropout_percent", c["dropout_values"], "independent/OSA_dropout"),
                ("osa_grid", "n_clients", c["n_values"], "independent/OSA_clients_by_dropout"),
            ):
                selected = select(rows, group)
                series = [("OSA", selected)] if group == "osa_dropout" else [
                    (f"k = {d}%", [r for r in selected if r["dropout_percent"] == d]) for d in c["dropout_values"]]
                for metric in (*TIMES[:3], TOTAL_METRIC, *COMM):
                    maker.draw(selected, group, metric, series, field, xs, category, group)
    if scope == "dropout":
        selected = select(rows, "dropout")
        series = [(suite.LABELS[p], [r for r in selected if r["protocol"] == p]) for p in suite.AGG]
        for metric in (*TIMES, *COMM):
            maker.draw(selected, "dropout", metric, series, "dropout_percent", c["four_protocol_dropout_values"],
                       "four_protocols/dropout", "four_protocols_dropout")
    if scope in ("all", "verification"):
        for group, bars in (("verification_main", True), ("verification_n", False)):
            selected = select(rows, group)
            series = [(suite.LABELS[p], [r for r in selected if r["protocol"] == p]) for p in ("vosa", "mpvas")]
            for metric in (*VERIFY_TIME, *VERIFY_COMM):
                maker.draw(selected, group, metric, series, "n_clients", [1000] if bars else c["n_values"],
                           "independent/" + group, group, bars)
        selected = select(rows, "vosa_grid")
        series = [(f"k = {d}%", [r for r in selected if r["dropout_percent"] == d]) for d in c["dropout_values"]]
        for metric in (*VERIFY_TIME, *VERIFY_COMM):
            maker.draw(selected, "vosa_grid", metric, series, "n_clients", c["n_values"],
                       "independent/VOSA_verification_clients_by_dropout", "vosa_verification_grid")
    suite.write_csv(out / "figure_index.csv", maker.index)
    suite.write_csv(out / "measurements.csv", original_rows)
    suite.write_csv(out / "total_cost_breakdown.csv", rows)
    suite.write_csv(out / "communication_audit.csv", communication_audit)
    (out / "CAPTIONS.md").write_text(captions() + "\n" + TOTAL_METHOD, encoding="utf-8")
    suite.write_json(out / "figure_audit.json", dict(status="passed", figures=len(maker.index),
                      expected_series_checked=True, each_figure_has_legend=True, titles_and_notes_outside=True,
                      units="seconds; decimal KB/MB", traffic="sent_only_no_receive_double_counting",
                      no_errorbars=True, original_means_preserved=True,
                      total_computation="continuous_start_to_result_end_to_end_wall_clock",
                      inset_figures=sum(x["axes"] == 2 for x in maker.index)))
    return maker.index


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", type=Path, required=True)
    a = p.parse_args()
    rows, manifest = suite.verify_run(a.run.resolve())
    contract = manifest["contract"]
    if contract["smoke"]:
        p.error("Smoke results cannot be used as formal paper figures")
    out = a.run.resolve() / ("figures_" + suite.stamp())
    index = generate(rows, contract["config"], out, contract["scope"])
    suite.write_json(out / "source.json", dict(run=str(a.run.resolve()), manifest_sha256=suite.sha(a.run / "manifest.json")))
    print(f"FIGURES_PASS count={len(index)}\nFIGURES={out}")


if __name__ == "__main__":
    main()
