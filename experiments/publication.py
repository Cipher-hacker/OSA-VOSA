"""Rebuild each figure from verified, independent runs at its measured settings."""
import csv
import json
import math
from pathlib import Path
import re
import statistics
import sys

from . import align, osa_grid

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


def load_comparison(comparison):
    old, aligned = align.modules(align.context(BENCH))
    comparison = align.absolute(comparison)
    state = align.read(comparison / "status.json")
    old.require(state["status"], "completed", "aligned comparison")
    for name in ("manifest", "measurements"):
        path = comparison / (name + (".json" if name == "manifest" else ".csv"))
        old.require(align.digest(path), state[name + "_sha256"], name)
    agg = align.verify_combined(old, aligned, comparison)
    compare_export(agg, comparison / "measurements.csv")
    old.require(len(agg), 216, "aligned comparison")
    return agg


def load_verification(verification, archived, cohort="both"):
    old = align.load(BENCH / "scripts/suite.py", "verification")
    verification = align.absolute(verification)
    config = align.read(BENCH / "config.json")
    if archived:
        class ProtocolMatcher:
            def __eq__(self, other):
                return other in ("VOSA-Packed-NTT-v1", "VOSA-verification-only-faithful")
            def __repr__(self):
                return "'VOSA-Packed-NTT-v1' or 'VOSA-verification-only-faithful'"

        old.EXPECTED["vosa"] = ProtocolMatcher()
        roles = retained_verification(old, verification, config)
    else:
        from . import sis_suite
        if sis_suite.is_current(verification):
            old = sis_suite.load(cohort)
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
    old.require(len(roles), len(old.plan(config, "verification")), "formal verification job count")
    return roles


def load_measurements(comparison, verification, archived):
    return load_comparison(comparison) + load_verification(verification, archived)


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


def verification_figure(spec):
    return any(j.startswith("vosa_") for group in spec["points"] for j in group["jobs"])


def computation_figure(spec):
    return spec["metric"].endswith("_s")


def generate_verification(verification, output, mpvas_baseline=None):
    from . import sis_suite
    if not sis_suite.is_current(verification):
        raise ValueError("Expected a completed two-matrix VOSA verification run")
    host = align.read(Path(verification) / "manifest.json")["contract"]["host"]
    cohort = host.get("verification_cohort", "both")
    if (cohort != "both") != bool(mpvas_baseline):
        raise ValueError("Use --mpvas-baseline with a VOSA-only run; a paired run already contains mPVAS")
    rows = load_verification(verification, archived=False, cohort=cohort)
    provenance = {"vosa_run": str(Path(verification).resolve()), "cohort": cohort,
                  "vosa_host": host["host"], "vosa_cpu": host["cpu_model"]}
    if mpvas_baseline:
        baseline = Path(mpvas_baseline).resolve()
        historical = load_verification(baseline, archived=not (baseline / "manifest.json").exists())
        mpvas = [r for r in historical if r["protocol"] == "mpvas"]
        if len(mpvas) != 18:
            raise ValueError("Expected 18 verified mPVAS measurements")
        hosts = sorted({r["host"] for r in mpvas})
        provenance.update(mpvas_run=str(baseline), mpvas_hosts=hosts,
                          matching_recorded_hostname=hosts == [host["host"]])
        if hosts != [host["host"]]:
            print("Timing comparison uses different recorded hostnames. Check hardware before reporting speedups.")
        rows.extend(mpvas)
    lookup = {r["job_id"]: r for r in rows}
    specs = [s for s in align.read(ROOT / "experiments/figures.json")
             if verification_figure(s) and computation_figure(s)
             and (cohort != "vosa-matched" or any(j.startswith("mpvas_") for g in s["points"] for j in g["jobs"]))]
    renderer = align.load(BENCH / "scripts/renderer.py", "verification_renderer")
    output.mkdir(parents=True, exist_ok=False)
    for spec in specs:
        points = points_for(spec, lookup)
        folder = output / spec["category"]
        write_csv(folder / "data" / (spec["stem"] + ".csv"), points)
        renderer.draw(points, spec, folder / "figures" / spec["stem"])
    write_csv(output / "verification-runs.csv", rows)
    (output / "sources.json").write_text(json.dumps(provenance, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"FIGURES={output} PNG={len(specs)} PDF={len(specs)}")


def generate(comparison, verification, output=None, archived=False, grid=None):
    rows = load_measurements(comparison, verification, archived)
    specs = align.read(ROOT / "experiments/figures.json")
    if grid is not None:
        if archived:
            raise ValueError("Historical replay cannot include new measurements")
        extra, full, config = osa_grid.load(BENCH, align.absolute(comparison), grid)
        compare_export(extra, grid / "measurements.csv")
        rows.extend(extra)
        specs.extend(osa_grid.specifications(full, config))
    # Historical replays retain their original figure contract. The new VOSA
    # comparison publishes compute measurements only.
    removed = []
    if not archived:
        from . import sis_suite
        if sis_suite.is_current(verification):
            removed = [s for s in specs if verification_figure(s) and not computation_figure(s)]
            specs = [s for s in specs if s not in removed]
    lookup = {job_key(r["job_id"]): r for r in rows}
    if len(lookup) != len(rows):
        raise ValueError("Duplicate run identities")
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
    expected_points, expected_figures = (1282, 82) if grid is not None else (1058, 66)
    expected_points -= sum(len(s["points"]) for s in removed)
    expected_figures -= len(removed)
    if expected or count != expected_points or len(specs) != expected_figures:
        raise ValueError("Incomplete publication figure coverage")
    if output is not None:
        # Keep different source schemas separate for downstream analysis.
        write_csv(output / "comparison-runs.csv", rows[:216])
        write_csv(output / "verification-runs.csv", rows[216:306])
        if grid is not None:
            write_csv(output / "osa-grid-additional-runs.csv", rows[306:])
            extra_ids = {r["job_id"] for r in extra}
            write_csv(output / "osa-grid-all-runs.csv", [
                dict(lookup[j.id], groups=";".join(groups),
                     measurement_origin="grid" if j.id in extra_ids else "comparison")
                for j, groups in full.items()])
            (output / "osa-grid-sources.json").write_text(json.dumps({
                "comparison": str(comparison.resolve()), "grid": str(grid.resolve()),
                "comparison_manifest_sha256": align.digest(comparison / "manifest.json"),
                "grid_manifest_sha256": align.digest(grid / "manifest.json"),
                "overlap": 24, "additional": len(extra_ids)
            }, indent=2), "utf-8")
        (output / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False), "utf-8")
        print(f"FIGURES={output} PNG={len(index)} PDF={len(index)}")
    print(f"MEASUREMENTS_PASS comparison=216 verification=90 grid_additional={len(rows)-306} figure_points={count}")

