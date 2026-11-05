#!/usr/bin/env python3
"""Align actual responders AND reconstruction thresholds; reuse unchanged measurements."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import shutil
import signal
import sys

REVISION = "committee-alignment-v1-20261104"
MERGED = "committee-alignment-merged-v1"
BASE_VERSION = "round-v2-20261101"
AGG = ("osa", "opa", "willow", "tacita")
SELF = Path(__file__).resolve()
_loaded = 0


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def absolute(path):
    path = Path(path).resolve()
    if sys.platform == "win32" and not str(path).startswith("\\\\?\\"):
        path = Path("\\\\?\\UNC\\" + str(path)[2:] if str(path).startswith("\\\\") else "\\\\?\\" + str(path))
    return path


def load(path, label):
    global _loaded
    _loaded += 1
    name = f"_alignment_{label}_{_loaded}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class Context:
    root: Path
    suite: Path
    plot: Path
    renderer: Path
    config: Path
    prepared: bool


def context(project):
    project = absolute(project)
    candidates = (project, project / ".work/OSA_Paper_Experiments_CPU_20261008")
    for root in candidates:
        if (root / "scripts/suite.py").is_file() and (root / "config.json").is_file():
            return Context(root, root / "scripts/suite.py", root / "scripts/plot.py",
                           root / "scripts/renderer.py", root / "config.json", True)
    layout = read(project / "layout.json")["packages"]["paper"]
    files = {r["original"]: project / r["source"] for r in layout["files"]}
    return Context(project / ".work" / layout["original_name"], files["scripts/suite.py"],
                   files["scripts/plot.py"], files["scripts/renderer.py"], files["config.json"], False)


def threshold(m):
    if not 40 <= m <= 100:
        raise ValueError("This supplement supports the original m=40..100 grid only")
    # Keep OSA d=16 and its strict t > m-d condition. Other protocols use the same t.
    return max((80 * m + 50) // 100, 17, m - 16 + 1)


def key(job):
    return tuple(getattr(job, k) for k in ("protocol", "n", "active", "m", "length", "repeat"))


def old_config(c):
    return {**{k: v for k, v in c.items() if k != "alignment"}, "schema": BASE_VERSION}


def modules(ctx):
    old = load(ctx.suite, "original")
    old.require(old.VERSION, BASE_VERSION, "supported original runner")
    aligned = load(ctx.suite, "aligned")
    aligned.ROOT = ctx.root
    aligned.VERSION = REVISION
    original_job = old.Job

    class AlignedJob(original_job):
        @property
        def t(self):
            if self.protocol not in AGG:
                raise ValueError("Only the four aggregation protocols are included")
            return threshold(self.m)

        @property
        def id(self):
            return super().id + f"_T{self.t}"

    aligned.Job = AlignedJob

    def plan(c, scope="comparison", smoke=False):
        if scope != "comparison":
            raise ValueError("Only comparison scope is supported")
        if smoke:
            return {AlignedJob("opa", 20, 16, m, 32, 1): ["smoke"] for m in c["m_values"]}
        jobs = {}
        for j, groups in old.plan(old_config(c), "comparison").items():
            if c["alignment"]["mode"] == "all" or j.t != threshold(j.m):
                jobs[AlignedJob(**asdict(j))] = groups
        return jobs

    aligned.plan = plan
    base_validate = aligned.validate_config

    def validate(c):
        base_validate(c)
        old.require(c["threads"], 32, "unchanged 32-thread limit")
        old.require(c["repeats"], 3, "three formal repetitions")
        meta = c["alignment"]
        old.require(meta["policy"], "same-reconstruction-and-response-count", "alignment policy")
        if meta["mode"] not in ("changed", "all"):
            raise ValueError("Invalid alignment mode")
        if meta["mode"] == "changed" and not meta["baseline_manifest_sha256"]:
            raise ValueError("Changed-only execution requires a baseline")
        for m in c["m_values"]:
            t = threshold(m)
            if not (16 < t <= m and t > m - 16):
                raise ValueError("Invalid shared threshold")

    aligned.validate_config = validate
    original_stamp = aligned.stamp
    aligned.stamp = lambda: "aligned_" + original_stamp()
    aligned.LIMITATIONS = list(old.LIMITATIONS) + [
        "This supplement aligns reconstruction threshold and actual response count for all four protocols.",
        "m=40,50,60,80,100 uses t=32,40,48,65,85 respectively; OSA d and OPA packing remain 16.",
        "Aligned thresholds do not certify identical security, input ranges or serialization across protocols.",
    ]
    return old, aligned


def find_baseline(ctx, requested=None, expected_hash=None):
    if requested:
        run = absolute(requested)
        if not (run / "manifest.json").is_file():
            raise ValueError(f"Missing baseline manifest: {run}")
    else:
        candidates = []
        for p in (ctx.root / "runs").glob("*/manifest.json"):
            m = read(p)
            if (m.get("contract", {}).get("version") == BASE_VERSION
                    and m["contract"]["scope"] == "comparison"
                    and not m["contract"]["smoke"]
                    and (p.parent / "status.json").is_file()
                    and read(p.parent / "status.json").get("status") == "completed"
                    and (expected_hash is None or digest(p) == expected_hash)):
                candidates.append(p.parent)
        if not candidates:
            raise ValueError("No completed original comparison run found; specify --baseline PATH. "
                             "On a new host use --fresh-all; old timing data must not be silently mixed.")
        run = sorted(candidates)[-1]
    if expected_hash and digest(run / "manifest.json") != expected_hash:
        raise ValueError("Baseline differs from the one recorded when the supplement started")
    return run


def validate_baseline(old, run):
    rows, manifest = old.verify_run(run)
    c = manifest["contract"]
    old.require(c["scope"], "comparison", "baseline scope")
    old.require(c["smoke"], False, "formal baseline required")
    old.require(c["config"]["threads"], 32, "baseline worker count")
    old.require(c["config"]["repeats"], 3, "baseline repetitions")
    return rows, manifest


def configuration(old, ctx, baseline_manifest=None, baseline_run=None, fresh=False):
    c = dict(baseline_manifest["contract"]["config"] if baseline_manifest else read(ctx.config))
    old.validate_config(c)
    c["schema"] = REVISION
    c["alignment"] = dict(policy="same-reconstruction-and-response-count", mode="all" if fresh else "changed",
                          baseline_manifest_sha256=digest(baseline_run / "manifest.json") if baseline_run else None)
    return c


def full_plan(old, aligned, c):
    return {aligned.Job(**asdict(j)): groups for j, groups in old.plan(old_config(c), "comparison").items()}


def install_preflight(old, aligned, ctx, baseline_manifest):
    def preflight(c, jobs, root):
        # Check all binaries, including ones whose previous measurements will be reused.
        host = old.preflight(old_config(c), old.plan(old_config(c), "comparison"), root)
        if baseline_manifest:
            previous = baseline_manifest["contract"]["host"]
            for field in ("host", "cpu_model", "available_cpus", "platform", "python", "source_sha256", "binary_sha256"):
                old.require(host[field], previous[field], "baseline reuse requires the same " + field)
        host["alignment_driver_sha256"] = digest(SELF)
        return host
    aligned.preflight = preflight


def describe(old, aligned, c):
    pending = aligned.plan(c)
    complete = full_plan(old, aligned, c)
    print(json.dumps(dict(thresholds={str(m): threshold(m) for m in c["m_values"]},
                          threads=c["threads"], repetitions=c["repeats"],
                          measurements_to_run=len(pending), per_protocol=dict(Counter(j.protocol for j in pending)),
                          unchanged_measurements_to_reuse=len(complete)-len(pending),
                          total_aligned_measurements=len(complete)), indent=2), flush=True)


@contextmanager
def recorded_host(host):
    original = platform.node
    platform.node = lambda: host
    try:
        yield
    finally:
        platform.node = original


def evidence_copy(suite, origin_run, destination, job):
    status_path = origin_run / "raw" / job.id / "status.json"
    status = read(status_path)
    attempt = (status_path.parent / status["attempt"]).resolve()
    target = destination / "raw" / job.id / status["attempt"]
    target.mkdir(parents=True, exist_ok=False)
    for relative, expected in status["evidence_sha256"].items():
        source = (attempt / relative).resolve()
        if not source.is_relative_to(attempt) or digest(source) != expected:
            raise ValueError("Invalid or changed source evidence")
        dest = target / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        if digest(dest) != expected:
            raise ValueError("Evidence copy failed")
    shutil.copyfile(status_path, target.parent / "status.json")


def merge(old, aligned, new_run, baseline_run=None):
    new_rows, new_manifest = aligned.verify_run(new_run)
    contract = new_manifest["contract"]
    if contract["smoke"]:
        raise ValueError("Smoke results cannot enter formal figures")
    c = contract["config"]
    old_rows, old_manifest = [], None
    if c["alignment"]["mode"] == "changed":
        if baseline_run is None:
            raise ValueError("Baseline required")
        old.require(digest(baseline_run / "manifest.json"), c["alignment"]["baseline_manifest_sha256"], "baseline identity")
        old_rows, old_manifest = validate_baseline(old, baseline_run)
        for field in ("host", "cpu_model", "available_cpus", "platform", "python", "source_sha256", "binary_sha256"):
            old.require(old_manifest["contract"]["host"][field], contract["host"][field], "merge host/code " + field)

    origins = {}
    for kind, run, manifest, rows in (("baseline", baseline_run, old_manifest, old_rows), ("retest", new_run, new_manifest, new_rows)):
        if manifest is None:
            continue
        klass = old.Job if kind == "baseline" else aligned.Job
        byid = {r["job_id"]: r for r in rows}
        for spec in manifest["contract"]["jobs"]:
            job = klass(**{k: v for k, v in spec.items() if k != "groups"})
            if kind == "baseline" and job.t != threshold(job.m):
                continue
            k = key(job)
            if k in origins:
                raise ValueError("Duplicate provenance for an aligned point")
            origins[k] = (kind, run, job, spec["groups"], byid[job.id])
    expected = full_plan(old, aligned, c)
    old.require(set(origins), {key(j) for j in expected}, "complete aligned coverage")
    out = new_run / ("combined_" + old.stamp())
    out.mkdir(parents=True, exist_ok=False)
    entries = []
    for job, groups in expected.items():
        kind, source, origin_job, origin_groups, row = origins[key(job)]
        old.require(int(row["responding_members"]), job.t, "actual responders at merge")
        evidence_copy(old, source, out / "evidence" / kind, origin_job)
        entries.append(dict(kind=kind, job=asdict(origin_job), origin_groups=origin_groups,
                            groups=groups, threshold=job.t))
    manifest = dict(schema=MERGED, config=c, host=contract["host"], inputs=dict(baseline=old_manifest, retest=new_manifest),
                    jobs=entries, unchanged_reused=sum(e["kind"] == "baseline" for e in entries),
                    newly_measured=sum(e["kind"] == "retest" for e in entries), driver_sha256=digest(SELF))
    old.write_json(out / "manifest.json", manifest)
    rows = verify_combined(old, aligned, out)
    old.write_csv(out / "measurements.csv", rows)
    old.write_json(out / "status.json", dict(status="completed", measurements=len(rows),
                    manifest_sha256=digest(out / "manifest.json"), measurements_sha256=digest(out / "measurements.csv")))
    return out, rows, manifest


def verify_combined(old, aligned, run):
    manifest = read(run / "manifest.json")
    old.require(manifest["schema"], MERGED, "merged result format")
    aligned.validate_config(manifest["config"])
    expected = {key(j): (j, groups) for j, groups in full_plan(old, aligned, manifest["config"]).items()}
    seen, rows = set(), []
    with recorded_host(manifest["host"]["host"]):
        for item in manifest["jobs"]:
            kind = item["kind"]
            if kind not in ("baseline", "retest"):
                raise ValueError("Unknown origin")
            suite = old if kind == "baseline" else aligned
            job = suite.Job(**item["job"])
            k = key(job)
            if k not in expected or k in seen:
                raise ValueError("Unexpected/duplicate merged measurement")
            target, groups = expected[k]
            old.require(job.t, target.t, "unchanged threshold for reuse")
            old.require(item["threshold"], target.t, "declared threshold")
            old.require(item["groups"], groups, "figure groups")
            row = suite.verify_job(run / "evidence" / kind, job, item["origin_groups"], manifest["config"]["threads"])
            old.require(row["responding_members"], target.t, "measured threshold")
            row = dict(row, groups=";".join(groups), source=f"evidence/{kind}/{row['source']}", measurement_origin=kind)
            rows.append(row)
            seen.add(k)
    old.require(seen, set(expected), "all four protocols and all repeats")
    return rows


def plots(ctx, aligned, run, rows, config):
    previous = {k: sys.modules.get(k) for k in ("suite", "renderer")}
    try:
        sys.modules["suite"] = aligned
        sys.modules["renderer"] = load(ctx.renderer, "renderer")
        plot = load(ctx.plot, "plot")
        original_captions = plot.captions()
        start = original_captions.index("OSA uses d=16.")
        end = original_captions.index("All role samples:", start)
        captions = (original_captions[:start] +
                    "OSA d=16; OPA packing=16. All four schemes use identical reconstruction and response counts.\n"
                    "m=40,50,60,80,100 uses t=32,40,48,65,85 respectively. Default m=50,t=40.\n"
                    "This changes the original OPA r=34 experimental configuration; it is not the author's original benchmark.\n"
                    "Only unchanged configurations on the same recorded host/code/binaries are reused; changed points are newly measured.\n" +
                    original_captions[end:])
        captions = captions.replace("New revision round-v2 only; never pool old measurements.",
                                    "Threshold-aligned comparison based on verified round-v2 evidence with explicit per-point provenance.")
        plot.captions = lambda: captions
        out = run / ("figures_" + aligned.stamp())
        index = plot.generate(rows, config, out, "comparison")
        aligned.write_json(out / "source.json", dict(run=str(run), manifest_sha256=digest(run / "manifest.json"),
                                                    driver_sha256=digest(SELF)))
        print(f"FIGURES_PASS count={len(index)}\nFIGURES={out}", flush=True)
    finally:
        for k, v in previous.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("plan", "run", "assemble", "verify", "status"))
    p.add_argument("--project", type=Path, required=True)
    p.add_argument("--baseline", type=Path)
    p.add_argument("--run", type=Path, help="Supplement/merged RESULTS directory for status, assemble or verify")
    p.add_argument("--resume", type=Path, help="Resume the printed supplement RESULTS directory")
    p.add_argument("--fresh-all", action="store_true", help="Measure all 216 on a new host; no previous timing reuse")
    a = p.parse_args()
    for name in ("project", "baseline", "run", "resume"):
        if getattr(a, name) is not None:
            setattr(a, name, absolute(getattr(a, name)))
    if a.fresh_all and a.baseline:
        p.error("--fresh-all and --baseline are mutually exclusive")
    if a.resume and a.action != "run":
        p.error("--resume is only valid with run")
    if a.run and a.action not in ("assemble", "verify", "status"):
        p.error("--run is only valid with assemble, verify or status")
    ctx = context(a.project)
    old, aligned = modules(ctx)
    if a.action == "status":
        if not a.run:
            p.error("status requires --run")
        state = read(a.run / "status.json")
        print(json.dumps(state, indent=2))
        if state.get("current_log"):
            log = a.run / state["current_log"]
            if log.is_file():
                print("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-3:]))
        return
    if a.action == "verify":
        if not a.run:
            p.error("verify requires --run")
        m = read(a.run / "manifest.json")
        if m.get("schema") == MERGED:
            state = read(a.run / "status.json")
            old.require(state["status"], "completed", "combined completion")
            for filename, field in (("manifest.json", "manifest_sha256"), ("measurements.csv", "measurements_sha256")):
                old.require(digest(a.run / filename), state[field], "combined export " + filename)
            rows = verify_combined(old, aligned, a.run)
            old.require(len(rows), state["measurements"], "combined export count")
        else:
            rows = aligned.verify_run(a.run)[0]
        print(f"VERIFIED measurements={len(rows)}")
        return
    baseline_run, baseline_manifest = None, None
    saved = read((a.resume or a.run) / "manifest.json")["contract"] if (a.resume or a.run) else None
    if saved and saved["version"] != REVISION:
        raise ValueError("Only this supplement's RESULTS can be resumed/assembled; originals remain unchanged")
    fresh = saved["config"]["alignment"]["mode"] == "all" if saved else a.fresh_all
    if not fresh:
        expected = saved["config"]["alignment"]["baseline_manifest_sha256"] if saved else None
        baseline_run = find_baseline(ctx, a.baseline, expected)
        _, baseline_manifest = validate_baseline(old, baseline_run)
        print(f"BASELINE={baseline_run}", flush=True)
    c = saved["config"] if saved else configuration(old, ctx, baseline_manifest, baseline_run, fresh)
    aligned.validate_config(c)
    describe(old, aligned, c)
    if a.action == "plan":
        return
    if a.action == "run":
        if not ctx.prepared:
            raise ValueError("Use the existing built Linux project with its .work directory; plan is cross-platform")
        install_preflight(old, aligned, ctx, baseline_manifest)
        before = digest(SELF)
        if sys.platform != "win32":
            def stop(signum, frame):
                raise KeyboardInterrupt(f"signal {signum}")
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGHUP, stop)
        if not a.resume:
            smoke = aligned.execute(c, "comparison", True, None, ctx.root)
            aligned.verify_run(smoke)
            print("OPA_ALIGNED_SMOKE_PASS", flush=True)
        new_run = aligned.execute(c, "comparison", False, a.resume, ctx.root)
        old.require(digest(SELF), before, "supplement changed while running")
    else:
        if not a.run:
            p.error("assemble requires --run")
        new_run = a.run.resolve()
    combined, rows, manifest = merge(old, aligned, new_run, baseline_run)
    print(f"ALIGNED_DATA_PASS measurements={len(rows)}\nCOMBINED={combined}", flush=True)
    plots(ctx, aligned, combined, rows, c)
    print("COMMITTEE_ALIGNMENT_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
