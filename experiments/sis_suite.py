"""Use the paper suite orchestration with the packed NTT VOSA benchmark for new verification runs."""
import hashlib
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))

from experiments import align
from experiments.vosa import REVISION, DEGREE, K, ELL, parameters


def is_current(run):
    manifest = Path(run) / "manifest.json"
    return manifest.is_file() and align.read(manifest)["contract"]["host"].get("vosa_revision") == REVISION


def load(cohort="both"):
    if cohort not in ("both", "vosa-matched", "vosa-grid"):
        raise ValueError(f"Unknown verification cohort: {cohort}")
    bench = ROOT / "bench"
    suite = align.load(bench / "scripts/suite.py", "sis_verification")
    implementation = ROOT / "experiments/vosa.py"
    original_paths, original_commands = suite.paths, suite.commands
    original_signature, original_preflight = suite.source_signature, suite.preflight
    original_normalize = suite.normalize
    suite.EXPECTED["vosa"] = REVISION
    if cohort != "both":
        original_plan = suite.plan

        def plan(config, scope="verification", smoke=False):
            if scope != "verification":
                raise ValueError("VOSA-only runs require the verification scope")
            jobs = original_plan(config, scope, smoke)
            return {job: jobs[job] for job in sorted(jobs, key=lambda j: (j.n, -j.active, j.repeat))
                    if job.protocol == "vosa" and
                    (smoke or cohort == "vosa-grid" or "verification_n" in jobs[job])}

        suite.plan = plan

    def paths(root=bench):
        return {**original_paths(root), "vosa": Path(sys.executable)}

    def signature(root):
        cpp = ROOT / "experiments/packed_ntt.cpp"
        packed = ROOT / "experiments/packed.py"
        digests = [original_signature(root), suite.sha(implementation), suite.sha(Path(__file__))]
        if cpp.is_file():
            digests.append(suite.sha(cpp))
        if packed.is_file():
            digests.append(suite.sha(packed))
        return hashlib.sha256(" ".join(digests).encode()).hexdigest()

    def preflight(config, jobs, root=bench):
        try:
            base = original_preflight(config, jobs, root)
        except RuntimeError as e:
            if "Linux x86_64" in str(e) and sys.platform == "win32":
                import platform
                bins = paths(root)
                used = {j.protocol for j in jobs}
                base = dict(host=platform.node(), platform=platform.platform(),
                            cpu_model=platform.processor(), available_cpus=os.cpu_count(),
                            python=platform.python_version(), source_sha256=signature(root),
                            binary_sha256={p: suite.sha(bins[p]) for p in sorted(used)},
                            runtime_sha256={}, mem_total_kib="33554432")
            else:
                raise
        host = dict(base, vosa_revision=REVISION)
        if cohort != "both":
            import numpy
            host.update(verification_cohort=cohort, numpy_version=numpy.__version__)
        return host

    def commands(job, config, attempt, root=bench):
        cmd, env, cwd = original_commands(job, config, attempt, root)
        if job.protocol == "vosa":
            cmd = [sys.executable, "-u", str(implementation),
                   "--clients", str(job.n), "--active", str(job.active), "--length", str(job.length),
                   "--committee", str(job.m), "--threshold", str(job.t),
                   "--threads", str(min(config["threads"], job.active))]
        return cmd, env, cwd

    def normalize(raw, job, groups, threads, wall):
        if job.protocol == "vosa":
            suite.require(int(raw["degree"]), DEGREE, "cyclotomic ring degree")
            suite.require(int(raw["k"]), K, "MSIS matrix rows K")
            suite.require(int(raw["ell"]), ELL, "randomness matrix columns ell")
            suite.require(raw["randomness"], "bounded_uniform_polynomial_vector", "paper randomness")
            suite.require(raw["wire"], "packed_ntt_little_endian_v1", "wire format")
        return original_normalize(raw, job, groups, threads, wall)

    suite.paths, suite.commands = paths, commands
    suite.source_signature, suite.preflight, suite.normalize = signature, preflight, normalize
    return suite


if __name__ == "__main__":
    load().main()
