"""Rebuild each figure from verified, independent runs at its measured settings."""
import csv
import json
import math
from pathlib import Path
import re
import statistics
import sys

from . import align

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "bench"


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def compare_export(rows, path):
    table = {r["job_id"]: r for r in rows}
    exported = read_csv(path)
    if len(table) != len(rows) or len(exported) != len(rows) or set(table) != {r["job_id"] for r in exported}:
        raise ValueError(f"Measurement coverage differs in {path}")
    for record in exported:
        row = table[record["job_id"]]
        for key, value in record.items():
            expected = "" if row[key] is None else str(row[key])
            if value != expected:
                raise ValueError(f"{path}: {row['job_id']} {key} differs from the run log")


def retained_verification(suite, directory, config):
    target = suite.plan(config, "verification")
    rows = []
    for job in target:
        attempts = list((directory / "raw" / job.id).glob("*/row.json"))
        if len(attempts) != 1:
            raise ValueError(f"Expected one retained attempt for {job.id}")
        path = attempts[0]
        previous = align.read(path)
        raw, _ = suite.read_raw(path.parent, job)
        with align.recorded_host(previous["host"]):
            row = suite.normalize(raw, job, previous["groups"].split(";"), config["threads"], previous["driver_wall_s"])
        row["source"] = path.parent.relative_to(directory).as_posix()
        for key, value in previous.items():
            suite.require(row[key], value, f"{job.id} {key}")
        rows.append(row)
    compare_export(rows, directory / "measurements.csv")
    return rows


def load_measurements(comparison, verification, archived):
    old, aligned = align.modules(align.context(BENCH))
    comparison, verification = map(align.absolute, (comparison, verification))
    state = align.read(comparison / "status.json")
    old.require(state["status"], "completed", "aligned comparison")
    for name in ("manifest", "measurements"):
        path = comparison / (name + (".json" if name == "manifest" else ".csv"))
        old.require(align.digest(path), state[name + "_sha256"], name)
    agg = align.verify_combined(old, aligned, comparison)
    compare_export(agg, comparison / "measurements.csv")
    config = align.read(BENCH / "config.json")
    if archived:
        roles = retained_verification(old, verification, config)
    else:
        roles, manifest = old.verify_run(verification)
        contract = manifest["contract"]
        old.require(contract["scope"], "verification", "separate verification run required")
        old.require(contract["smoke"], False, "formal verification")
        old.require(contract["config"], config, "verification configuration")
        compare_export(roles, verification / "measurements.csv")
    for r in roles:
        raw = old.parse_line((verification / r["source"] / "process.log").read_text("utf-8"))
        if r["protocol"] == "vosa":
            clients = float(raw["client_compute_total_s"])
        else:
            clients = (r["active_clients"] * float(raw["active_client_compute_mean_s"])
                       + (r["n_clients"] - r["active_clients"]) * float(raw["dropout_client_compute_mean_s"]))
        r["total_computation_measured_s"] = clients + r["server_compute_s"] + r["verifier_compute_s"]
    old.require((len(agg),len(roles)), (216,90), "formal job count")
    return agg + roles


def job_key(name):
    # Alignment appends the checked responder count to freshly executed job IDs.
    return re.sub(r"_T\d+$", "", name)


def points_for(spec, lookup):
    points = []
    for group in spec["points"]:
        rows = [lookup[job_key(j)] for j in group["jobs"]]
        if sorted(r["repeat"] for r in rows) != [1, 2, 3]:
            raise ValueError(f"Three independent repeats required in {spec['name']}")
        values = [r[spec["metric"]] for r in rows]
        mean = statistics.mean(values)
        sd = statistics.stdev(values)
        divisor = spec["divisor"]
        points.append(dict(series=group["series"], x=group["x"], metric=spec["metric"],
                           raw_mean=mean, raw_sample_sd=sd, value=mean/divisor, sample_sd=sd/divisor,
                           divisor=divisor, repeats=len(rows), job_ids=";".join(r["job_id"] for r in rows)))
    return points


def generate(comparison, verification, output=None, archived=False):
    rows = load_measurements(comparison, verification, archived)
    lookup = {job_key(r["job_id"]): r for r in rows}
    if len(lookup) != len(rows):
        raise ValueError("Duplicate run identities")
    specs = align.read(ROOT / "experiments/figures.json")
    expected = {}
    if archived:
        for p in read_csv(ROOT / "reference/figure-values.csv"):
            expected[p["figure"], p["series"], p["x"]] = p
    if output is not None:
        renderer = align.load(BENCH / "scripts/renderer.py", "publication_renderer")
        output.mkdir(parents=True, exist_ok=False)
    count = 0
    index = []
    for spec in specs:
        points = points_for(spec, lookup)
        if archived:
            for p in points:
                entry = expected.pop((spec["name"], p["series"], p["x"]))
                for key in ("raw_mean", "sample_sd", "value"):
                    if not math.isclose(float(entry[key]), p[key], rel_tol=1e-12, abs_tol=1e-10):
                        raise ValueError(f"{spec['name']} {p['series']} x={p['x']} {key} mismatch")
        if output is not None:
            folder = Path(spec["scope"]) / spec["category"]
            stem = folder / "figures" / spec["stem"]
            data = folder / "data" / (spec["stem"] + ".csv")
            info = renderer.draw(points, spec, output / stem)
            write_csv(output / data, points)
            index.append(dict(figure=spec["name"], png=stem.with_suffix(".png").as_posix(),
                              pdf=stem.with_suffix(".pdf").as_posix(), data=data.as_posix(), **info))
        count += len(points)
    if expected or count != 1058 or len(specs) != 66:
        raise ValueError("Incomplete publication figure coverage")
    if output is not None:
        # Keep different source schemas separate for downstream analysis.
        write_csv(output / "comparison-runs.csv", rows[:216])
        write_csv(output / "verification-runs.csv", rows[216:])
        (output / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False), "utf-8")
        print(f"FIGURES={output} PNG=66 PDF=66")
    print(f"MEASUREMENTS_PASS comparison=216 verification=90 figure_points={count}")

