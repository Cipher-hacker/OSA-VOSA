"""Coverage, metric definitions and long-run recovery for the final experiment entry."""
from collections import Counter
from contextlib import nullcontext
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import artifact
from experiments import align, osa_grid, publication


class FinalPlanTests(unittest.TestCase):
    def setUp(self):
        self.suite = align.load(artifact.SUITE, "test_full")
        self.config = align.read(artifact.BENCH / "config.json")
        self.full, self.missing = osa_grid.plans(self.suite, self.config)

    def test_coverage_and_final_thresholds(self):
        comparison = self.suite.plan(self.config, "comparison")
        verification = self.suite.plan(self.config, "verification")
        union = set(comparison) | set(self.missing) | set(verification)
        self.assertEqual(len(union), len(comparison)+len(self.missing)+len(verification))
        self.assertEqual(Counter(j.protocol for j in union),
                         dict(osa=102, opa=54, willow=54, tacita=54, vosa=72, mpvas=18))
        self.assertTrue(all(j.protocol=="osa" and j.t==40 and j.rss==16 and
                            j.m==50 and j.length==10000 for j in self.missing))
        ordered, config, _ = osa_grid.module(artifact.BENCH)
        self.assertEqual(set(self.missing), set(self.suite.Job(**vars(j)) for j in ordered.plan(config, osa_grid.SCOPE)))
        with self.assertRaises(ValueError):
            ordered.plan(config, "comparison")

    def test_grid_jobs_and_plot_definitions(self):
        archive = publication.read_csv(ROOT/"reference/comparison/measurements.csv")
        recorded = {publication.job_key(row["job_id"]) for row in archive}
        self.assertEqual({j.id for j in self.full} & recorded, {j.id for j in self.full if j not in self.missing})
        specs = osa_grid.specifications(self.full, self.config)
        self.assertEqual(len(specs), 16)
        self.assertEqual(sum(len(s["points"]) for s in specs), 224)
        metrics = {s["metric"]:s for s in specs}
        self.assertEqual(metrics["total_bytes"]["ylabel"], "Total communication (MB)")
        self.assertEqual(metrics["end_to_end_s"]["ylabel"], "Total time (s)")
        target = {j.id:j for j in self.full}
        for s in specs:
            for p in s["points"]:
                jobs = [target[name] for name in p["jobs"]]
                self.assertEqual([j.repeat for j in jobs], [1,2,3])
                j = jobs[0]
                expected_rate = int(p["x"]) if p["series"] == "OSA" else int(p["series"][4:-1])
                expected_n = 1000 if p["series"] == "OSA" else int(p["x"])
                self.assertEqual((j.n,j.active), (expected_n,expected_n*(100-expected_rate)//100))

    def test_incompatible_grid_machine_rejected(self):
        host = align.read(ROOT/"reference/comparison/manifest.json")["host"]
        osa_grid.check_host(self.suite, host, host)
        for modified in (dict(host, host="unrelated"), dict(host, binary_sha256={"osa":"changed"})):
            with self.assertRaises(ValueError):
                osa_grid.check_host(self.suite, host, modified)

    def test_grid_uses_the_aligned_native_osa_command(self):
        context = align.context(artifact.BENCH)
        original, aligned = align.modules(context)
        config = align.configuration(original, context, fresh=True)
        attempt = artifact.BENCH/"test-attempt"
        for job in self.full:
            aligned_job = aligned.Job(**vars(job))
            baseline = self.suite.commands(job, self.config, attempt, artifact.BENCH)
            expected = aligned.commands(aligned_job, config, attempt, artifact.BENCH)
            self.assertEqual(baseline, expected)

    def test_interrupted_full_workflow_resumes_and_executes_training(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "output"
            def comparison(raw, record):
                self.assertIsNone(raw)
                record(root/"raw_comparison")
                return root/"combined"
            def grid(command, tag, record):
                record(root/"raw_grid")
                return root/"raw_grid"
            raw_verify = root/"raw_verification"
            def fail_once(raw, record):
                record(raw_verify)
                raise RuntimeError("simulated interruption")
            def finish(raw, record):
                self.assertEqual(raw, raw_verify)
                return raw_verify
            self.suite.Lock = lambda path: nullcontext()
            with patch.object(align, "load", return_value=self.suite), \
                 patch.object(artifact, "run_comparison", side_effect=comparison) as comp, \
                 patch.object(artifact, "tagged", side_effect=grid) as supplement, \
                 patch.object(artifact, "generate") as render, patch.object(artifact, "train") as train:
                with patch.object(artifact,"run_verification",side_effect=fail_once):
                    with self.assertRaisesRegex(RuntimeError,"simulated interruption"):
                        artifact.whole_experiment("all",dest,False)
                with patch.object(artifact,"run_verification",side_effect=finish):
                    artifact.whole_experiment("all",dest,True)
                self.assertEqual(comp.call_count,1)
                self.assertEqual(supplement.call_count,1)
                render.assert_called_once()
                train.assert_called_once_with(dest/"training",resume=False)
                state = json.loads((dest/"workflow.json").read_text())
                self.assertEqual(Path(state["verification"]),raw_verify)
