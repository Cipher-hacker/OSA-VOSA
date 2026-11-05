#!/usr/bin/env python3
"""Run the archived paper experiments, replay evidence, or train the optional FL models."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import subprocess
import sys

from experiments import align, datasets
from experiments.publication import BENCH, ROOT, generate

ALIGN = ROOT / "experiments/align.py"
SUITE = BENCH / "scripts/suite.py"
PY = BENCH / ".runtime/python/bin/python"
FL_PY = BENCH / ".runtime/fl-python/bin/python"


def call(command, cwd=ROOT, env=None):
    subprocess.run([str(x) for x in command], cwd=cwd, env=env, check=True)


def tagged(command, tag):
    """Stream long jobs to the terminal while retaining their exact result directory."""
    last = None
    with subprocess.Popen([str(x) for x in command], cwd=BENCH, stdout=subprocess.PIPE,
                          text=True, encoding="utf-8", errors="replace") as proc:
        for line in proc.stdout:
            print(line, end="", flush=True)
            if line.startswith(tag + "="):
                last = Path(line.strip().split("=", 1)[1])
        if proc.wait():
            raise subprocess.CalledProcessError(proc.returncode, command)
    if last is None or not last.is_dir():
        raise RuntimeError(f"Successful process did not report a valid {tag} path")
    return last


def run_comparison(resume=None):
    args = ["--resume", resume.resolve()] if resume else ["--fresh-all"]
    return tagged([PY, "-u", ALIGN, "run", "--project", BENCH, *args], "COMBINED")


def run_verification(resume=None):
    args = ["--resume", resume.resolve()] if resume else []
    return tagged([PY, "-u", SUITE, "run", "--scope", "verification", *args], "RESULTS")


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
    verification = tagged([PY, "-u", SUITE, "run", "--scope", "verification", "--smoke"], "RESULTS")
    old.verify_run(verification)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("plan", "verify", "test", "build", "smoke", "setup-training"):
        sub.add_parser(name)
    sub.add_parser("setup").add_argument("--install-system", action="store_true")
    for name in ("comparison", "verification", "training"):
        sub.add_parser(name).add_argument("--resume", type=Path)
    for name in ("paper", "replay", "figures"):
        p = sub.add_parser(name)
        p.add_argument("--output", type=Path)
        if name == "figures":
            p.add_argument("--comparison", required=True, type=Path, help="COMBINED directory")
            p.add_argument("--verification", required=True, type=Path, help="formal verification RESULTS directory")
    args = parser.parse_args()
    mode = args.action
    if mode == "plan":
        call([sys.executable, ALIGN, "plan", "--project", BENCH, "--fresh-all"])
        call([sys.executable, SUITE, "plan", "--scope", "verification"])
    elif mode == "verify":
        validation()
    elif mode == "test":
        tests()
    elif mode in ("replay", "figures"):
        archived = mode == "replay"
        comparison = ROOT / "reference/comparison" if archived else args.comparison
        verification = ROOT / "reference/verification" if archived else args.verification
        generate(comparison, verification, (args.output or new_output(mode)).resolve(), archived)
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
            run_verification(args.resume)
        elif mode == "paper":
            output = (args.output or new_output("paper")).resolve()
            if output.exists():
                parser.error(f"Output path is already in use: {output}")
            comparison = run_comparison()
            verification = run_verification()
            generate(comparison, verification, output)


if __name__ == "__main__":
    main()

