#!/usr/bin/env python3
"""Run the archived paper experiments, replay evidence, or train the optional FL models."""
import argparse
import csv
from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import subprocess
import sys

from experiments import align, datasets, osa_grid
from experiments.publication import (BENCH, ROOT, compare_export, generate, generate_verification,
                                    load_comparison, load_measurements, read_csv)

ALIGN = ROOT / "experiments/align.py"
SUITE = BENCH / "scripts/suite.py"
SIS_SUITE = ROOT / "experiments/sis_suite.py"
PY = BENCH / ".runtime/python/bin/python"
FL_PY = BENCH / ".runtime/fl-python/bin/python"


def call(command, cwd=ROOT, env=None):
    subprocess.run([str(x) for x in command], cwd=cwd, env=env, check=True)


def tagged(command, tag, on_run=None):
    """Stream long jobs to the terminal while retaining their exact result directory."""
    last = None
    with subprocess.Popen([str(x) for x in command], cwd=BENCH, stdout=subprocess.PIPE,
                          text=True, encoding="utf-8", errors="replace") as proc:
        for line in proc.stdout:
            print(line, end="", flush=True)
            if on_run and line.startswith("RESULTS="):
                run = Path(line.strip().split("=", 1)[1])
                contract = align.read(run / "manifest.json").get("contract", {})
                if contract.get("smoke") is False:
                    on_run(run)
            if line.startswith(tag + "="):
                last = Path(line.strip().split("=", 1)[1])
        if proc.wait():
            raise subprocess.CalledProcessError(proc.returncode, command)
    if last is None or not last.is_dir():
        raise RuntimeError(f"Successful process did not report a valid {tag} path")
    return last


def run_comparison(resume=None, on_run=None):
    args = ["--resume", resume.resolve()] if resume else ["--fresh-all"]
    return tagged([PY, "-u", ALIGN, "run", "--project", BENCH, *args], "COMBINED", on_run)


def run_verification(resume=None, on_run=None, smoke=False):
    args = ["--resume", resume.resolve()] if resume else []
    return tagged([PY, "-u", SIS_SUITE, "run", "--scope", "verification",
                   *(["--smoke"] if smoke else []), *args], "RESULTS", on_run)


def validation():
    old, _ = align.modules(align.context(BENCH))
    old.verify_package(BENCH)
    reference = align.read(ROOT / "reference/comparison/manifest.json")
    old.require(old.source_signature(BENCH), reference["host"]["source_sha256"], "paper benchmark source fingerprint")
    old.require(align.digest(ALIGN), reference["driver_sha256"], "alignment driver fingerprint")
    datasets.check_data(ROOT)
    generate(ROOT / "reference/comparison", ROOT / "reference/verification", archived=True)


def tests():
    env = dict(os.environ, ALIGN_PROJECT=str(BENCH), PYTHONDONTWRITEBYTECODE="1")
    for cwd, folder, pattern in ((BENCH, "tests", "test_*.py"),
                                 (BENCH / "tacita", "tests", "test_fig7_*.py"),
                                 (ROOT, "experiments/tests", "test_*.py"),
                                 (BENCH / "project", "federated_training/tests", "test_config_only.py")):
        call([sys.executable, "-m", "unittest", "discover", "-s", folder, "-p", pattern, "-v"], cwd, env)


def smoke():
    # Use the final response policy also for the four-protocol smoke measurement.
    ctx = align.context(BENCH)
    old, aligned = align.modules(ctx)
    cfg = align.configuration(old, ctx, fresh=True)
    formal_plan = aligned.plan

    def smoke_plan(config, scope="comparison", smoke=False):
        if smoke:
            return {aligned.Job(p, 20, 16, 50, 32, 1): ["smoke"] for p in align.AGG}
        return formal_plan(config, scope, smoke)

    aligned.plan = smoke_plan
    run = aligned.execute(cfg, "comparison", True, None, BENCH)
    aligned.verify_run(run)
    from experiments import sis_suite
    verification = run_verification(smoke=True)
    sis_suite.load().verify_run(verification)
    print("NATIVE_SMOKE_PASS protocols=6")


def new_output(label):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    return ROOT / "outputs" / f"{label}_{stamp}"


def train(output, resume=False):
    datasets.check_data(ROOT)
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="", OMP_NUM_THREADS="4", MKL_NUM_THREADS="4",
               OPENBLAS_NUM_THREADS="1", OSA_PARALLEL_CHUNKS="8")
    if resume:
        if not output.is_dir():
            raise SystemExit("--resume must name an existing training output directory")
    else:
        output.mkdir(parents=True, exist_ok=False)
    print(f"TRAINING={output}", flush=True)
    project = BENCH / "project"
    for model in ("cifar10_alexnet_bn", "mnist_lenet5"):
        config = project / "federated_training/configs" / (model + ".json")
        for protocol in ("osa", "plain"):
            path = output / model / protocol
            args = ["--resume"] if resume and (path / "metrics.csv").is_file() else []
            call([FL_PY, "-u", "-m", "federated_training.train", "--config", config,
                  "--protocol", protocol, "--device", "cpu", "--data-root", ROOT / "datasets",
                  "--output-root", output, *args], project, env)
            with (path / "metrics.csv").open(encoding="utf-8-sig", newline="") as handle:
                records = list(csv.DictReader(handle))
            rounds = align.read(config)["rounds"]
            if ([int(r["round"]) for r in records] != list(range(1, rounds + 1))
                    or any(r["protocol"] != protocol or r["device"] != "cpu" for r in records)):
                raise RuntimeError(f"Incomplete training metrics: {path}")
    print(f"TRAINING_PASS models=2 protocols=2 rounds_per_run=300 output={output}", flush=True)


def previous_paper_runs(export, runs):
    """Locate and verify the two native runs used to produce an earlier paper export."""
    def match(name, pattern, count):
        recorded = read_csv(export / name)
        if len(recorded) != count:
            raise ValueError(f"Expected {count} rows in {export / name}")
        for directory in sorted(runs.glob(pattern), reverse=True):
            csv_file = directory / "measurements.csv"
            if not csv_file.is_file():
                continue
            try:
                compare_export(recorded, csv_file)
            except (KeyError, ValueError):
                continue
            return directory
        raise ValueError(f"No original run under {runs} matches {export / name}")

    comparison = match("comparison-runs.csv", "*/combined_*", 216)
    verification = match("verification-runs.csv", "paper_*", 90)
    rows = load_measurements(comparison, verification, archived=False)
    compare_export(rows[:216], export / "comparison-runs.csv")
    compare_export(rows[216:], export / "verification-runs.csv")
    return comparison, verification


def whole_experiment(mode, dest, resume, from_paper=None):
    """Checkpoint the native result directories before a long stage can be interrupted."""
    suite = align.load(SUITE, "workflow")
    dest.mkdir(parents=True, exist_ok=resume or from_paper is not None)
    state_file = dest / "workflow.json"
    with suite.Lock(dest / ".workflow.lock"):
        state = align.read(state_file) if resume or state_file.exists() else {"mode": mode}
        suite.require(state["mode"], mode, "resume action")
        if from_paper is not None:
            if "from_paper" in state:
                suite.require(state["from_paper"], str(from_paper), "previous paper export")
            else:
                suite.require(state, {"mode": mode}, "new continuation workflow")
                comparison, verification = previous_paper_runs(from_paper, BENCH / "runs")
                from experiments import sis_suite
                state.update(from_paper=str(from_paper), comparison=str(comparison))
                if (verification / "manifest.json").is_file() and not sis_suite.is_current(verification):
                    print("Reusing four-protocol comparison; measuring the revised VOSA verification stage.")
                else:
                    state["verification"] = str(verification)
                print(f"COMBINED={comparison}", flush=True)
        def save(key, value):
            state[key] = str(value)
            suite.write_json(state_file, state)
        def stage(name, runner):
            if name not in state:
                raw = state.get(name + "_raw")
                result = runner(Path(raw) if raw else None, lambda p: save(name + "_raw", p))
                save(name, result)
            return Path(state[name])
        suite.write_json(state_file, state)
        if "verification" in state and (Path(state["verification"]) / "manifest.json").is_file():
            from experiments import sis_suite
            if not sis_suite.is_current(state["verification"]):
                raise ValueError("Legacy verification in workflow; start a new workflow or use --from-paper.")
        print(f"WORKFLOW={dest}", flush=True)
        comparison = stage("comparison", run_comparison)
        def supplement(raw, record):
            args = ["--resume", raw] if raw else []
            return tagged([PY, "-u", ROOT / "artifact.py", "grid", "--comparison", comparison, *args],
                          "RESULTS", record)
        grid = stage("grid", supplement)
        verification = stage("verification", run_verification)
        if "figures" not in state:
            figures = dest / ("figures_" + suite.stamp())
            generate(comparison, verification, figures, grid=grid)
            save("figures", figures)
        if mode == "all" and "training" not in state:
            folder = dest / "training"
            train(folder, resume=folder.exists())
            save("training", folder)
        print(f"COMPLETE={dest}\nFIGURES={state['figures']}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("plan", "verify", "test", "build", "smoke", "setup-training"):
        sub.add_parser(name)
    sub.add_parser("setup").add_argument("--install-system", action="store_true")
    for name in ("comparison", "training"):
        sub.add_parser(name).add_argument("--resume", type=Path)
    verification = sub.add_parser("verification")
    verification.add_argument("--resume", type=Path)
    verification.add_argument("--smoke", action="store_true")
    sub.add_parser("vosa-check", help="portable end-to-end smoke test of the two-matrix SIS commitment")
    verification_figures = sub.add_parser("verification-figures", help="plot only the revised verification stage")
    verification_figures.add_argument("--verification", required=True, type=Path)
    verification_figures.add_argument("--mpvas-baseline", type=Path, help="reuse the mPVAS rows from a verified earlier run")
    verification_figures.add_argument("--output", type=Path)
    grid_parser = sub.add_parser("grid")
    grid_parser.add_argument("--comparison", required=True, type=Path)
    grid_parser.add_argument("--resume", type=Path)
    for name in ("all", "paper", "replay", "figures"):
        p = sub.add_parser(name)
        dest_args = p.add_mutually_exclusive_group()
        dest_args.add_argument("--output", type=Path)
        if name in ("all", "paper"):
            dest_args.add_argument("--resume", type=Path)
            dest_args.add_argument("--from-paper", type=Path, help="continue from a completed 66-figure paper export")
        if name == "figures":
            p.add_argument("--comparison", required=True, type=Path, help="COMBINED directory")
            p.add_argument("--verification", required=True, type=Path, help="formal verification RESULTS directory")
            p.add_argument("--grid", type=Path, help="OSA-only 48-run supplement for the extended figures")
    args = parser.parse_args()
    mode = args.action
    if mode == "vosa-check":
        call([sys.executable, ROOT / "experiments/vosa_sis.py"])
    elif mode == "verification-figures":
        generate_verification(args.verification, (args.output or new_output(mode)).resolve(), args.mpvas_baseline)
    elif mode == "plan":
        call([sys.executable, ALIGN, "plan", "--project", BENCH, "--fresh-all"])
        call([sys.executable, SIS_SUITE, "plan", "--scope", "verification"])
        suite = align.load(SUITE, "plan")
        config = align.read(BENCH / "config.json")
        full, missing = osa_grid.plans(suite, config)
        models = [align.read(p) for p in sorted((BENCH / "project/federated_training/configs").glob("*.json"))]
        print(f"OSA_GRID measured={len(missing)} reused={len(full)-len(missing)} total={len(full)}")
        print(f"PAPER unique_protocol_runs={len(suite.plan(config, 'all'))} figures=82")
        for model in models:
            print(f"TRAINING {model['dataset']} {model['model']} OSA + Plain, {model['rounds']} rounds each")
    elif mode == "verify":
        validation()
    elif mode == "test":
        tests()
    elif mode in ("replay", "figures"):
        archived = mode == "replay"
        comparison = ROOT / "reference/comparison" if archived else args.comparison
        verification = ROOT / "reference/verification" if archived else args.verification
        generate(comparison, verification, (args.output or new_output(mode)).resolve(), archived,
                 grid=getattr(args, "grid", None))
    else:
        if platform.system() != "Linux" or platform.machine() != "x86_64":
            parser.error("Native experiments require Linux x86_64")
        if mode == "setup":
            source = os.environ.get("PYTHON_SOURCE", sys.executable)
            call([source, "-c", "import sys; assert sys.version_info[:2] == (3,10), 'Python 3.10.x required by Charm'"])
            call(["bash", BENCH / "scripts/setup.sh", *(["--install-system"] if args.install_system else [])],
                 BENCH, dict(os.environ, PYTHON_SOURCE=source))
            return
        if not PY.is_file():
            parser.error("Run setup first")
        if Path(sys.prefix).resolve() != PY.parents[1].resolve():
            os.execv(str(PY), [str(PY), "-u", str(ROOT / "artifact.py"), *sys.argv[1:]])
        if mode == "build":
            call(["bash", BENCH / "scripts/build.sh"], BENCH, dict(os.environ, INCLUDE_VERIFICATION="1"))
        elif mode == "setup-training":
            call(["bash", ROOT / "experiments/setup_training.sh"])
        elif mode == "training":
            if not FL_PY.is_file():
                parser.error("Run setup-training first")
            train(args.resume.resolve() if args.resume else new_output("training"), bool(args.resume))
        elif mode == "smoke":
            smoke()
        elif mode == "comparison":
            run_comparison(args.resume)
        elif mode == "verification":
            run_verification(args.resume, smoke=args.smoke)
        elif mode == "grid":
            directory = align.absolute(args.comparison)
            load_comparison(directory)
            osa_grid.run(BENCH, directory, args.resume)
        elif mode in ("paper", "all"):
            if mode == "all" and not FL_PY.is_file():
                parser.error("Run setup-training first")
            previous = args.from_paper.resolve() if args.from_paper else None
            if previous:
                output = ROOT / "outputs" / f"{mode}_from_{previous.name}"
            else:
                output = (args.resume or args.output or new_output(mode)).resolve()
            whole_experiment(mode, output, resume=bool(args.resume), from_paper=previous)


if __name__ == "__main__":
    main()

