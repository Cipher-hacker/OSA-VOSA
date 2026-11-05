"""OSA-only final-profile points that the four-protocol comparison does not cover."""
from . import align

SCOPE = "osa_grid_final_v1"
GRID = "osa_grid"
SLICE = "osa_dropout"


def plans(suite, config):
    original = suite.plan(config, "all")
    comparison = suite.plan(config, "comparison")
    grid = {job: [g for g in groups if g in (GRID, SLICE)]
            for job, groups in original.items() if GRID in groups}
    missing = {job: groups for job, groups in grid.items() if job not in comparison}
    verification = suite.plan(config, "verification")
    if set(original) != set(comparison) | set(missing) | set(verification):
        raise ValueError("Final scopes do not cover the underlying experiment plan")
    suite.require((len(comparison), len(verification), len(grid), len(missing)),
                  (216, 90, 72, 48), "deduplicated formal plan")
    return grid, missing


def module(bench):
    suite = align.load(bench / "scripts/suite.py", "osa_grid")
    config = align.read(bench / "config.json")
    full, missing = plans(suite, config)
    def plan(c, scope, smoke=False):
        suite.require(c, config, "final profile")
        suite.require(scope, SCOPE, "OSA extension scope")
        suite.require(smoke, False, "formal OSA grid")
        return missing.copy()
    suite.plan = plan
    return suite, config, full


def check_host(suite, comparison_host, grid_host):
    for key in ("host", "platform", "cpu_model", "available_cpus", "python", "source_sha256"):
        suite.require(grid_host[key], comparison_host[key], "OSA grid comparison " + key)
    suite.require(grid_host["binary_sha256"]["osa"], comparison_host["binary_sha256"]["osa"],
                  "same OSA executable")


def run(bench, comparison, resume):
    suite, config, _ = module(bench)
    comparison_host = align.read(comparison / "manifest.json")["host"]
    native_preflight = suite.preflight
    def preflight(c, jobs, root):
        host = native_preflight(c, jobs, root)
        check_host(suite, comparison_host, host)
        return host
    suite.preflight = preflight
    result = suite.execute(config, SCOPE, False, resume, bench)
    suite.verify_run(result)
    return result


def load(bench, comparison, directory):
    suite, config, full = module(bench)
    rows, manifest = suite.verify_run(align.absolute(directory))
    contract = manifest["contract"]
    suite.require(contract["config"], config, "final grid parameters")
    check_host(suite, align.read(comparison / "manifest.json")["host"], contract["host"])
    return rows, full, config


METRICS = (
    ("client_compute_s", "单用户计算时间", "Running time (s)", 1),
    ("committee_compute_s", "单回应委员计算时间", "Running time (s)", 1),
    ("server_compute_s", "服务器计算时间", "Running time (s)", 1),
    ("end_to_end_s", "整轮完成时间", "Total time (s)", 1),
    ("client_send_bytes_mean", "单用户发送量", "Communication (KB)", 1000),
    ("committee_send_bytes_mean", "单回应委员发送量", "Communication (KB)", 1000),
    ("server_sent_bytes", "服务器发送量", "Communication (MB)", 1000000),
    ("total_bytes", "系统总发送量", "Total communication (MB)", 1000000),
)


def specifications(full, config):
    """Derive the fixed-n slice and the n-by-dropout grid from the same 72 runs."""
    grid = {(j.n, 100 * (j.n - j.active) // j.n, j.repeat): j for j in full}
    rates = config["dropout_values"]
    def point(label, x, n, rate):
        jobs = [grid[n, rate, rep].id for rep in range(1, config["repeats"] + 1)]
        return dict(series=label, x=str(x), jobs=jobs)
    slice_points = [point("OSA", k, config["n_clients"], k) for k in rates]
    grid_labels = [f"k = {k}%" for k in rates]
    grid_points = [point(label, n, n, k) for label, k in zip(grid_labels, rates)
                   for n in config["n_values"]]
    layouts = ((SLICE, "OSA_固定参与人数掉线率变化", "dropout_percent", ["OSA"], slice_points),
               (GRID, "OSA_参与人数与掉线率", "n_clients", grid_labels, grid_points))
    return [dict(name=f"{group}_{field}", category=category, stem=f"{category}_{role}",
                 labels=labels, xfield=xf, points=points, ylabel=ylabel, metric=field,
                 scope="paper", repeats=config["repeats"], bars=False, divisor=divisor)
            for group, category, xf, labels, points in layouts
            for field, role, ylabel, divisor in METRICS]
