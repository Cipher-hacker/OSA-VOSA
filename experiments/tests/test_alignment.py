"""Runner checks only; these do not substitute for native Linux protocol tests."""
import copy
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import align


class AlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project = os.environ.get("ALIGN_PROJECT", str(ROOT.parent / "OSA_Updated_Code_20261101"))
        cls.ctx = align.context(project)

    def setUp(self):
        self.old, self.new = align.modules(self.ctx)
        self.c = align.configuration(self.old, self.ctx, fresh=True)

    def changed(self):
        c = copy.deepcopy(self.c)
        c["alignment"].update(mode="changed", baseline_manifest_sha256="test-only-hash")
        return c

    def test_shared_thresholds_and_coverage(self):
        expected = {40: 32, 50: 40, 60: 48, 80: 65, 100: 85}
        for m, t in expected.items():
            for protocol in align.AGG:
                self.assertEqual(self.new.Job(protocol, 1000, 800, m, 10000, 1).t, t)
            self.assertGreater(t, m - 16)
            self.assertGreater(t, 16)
        jobs = self.new.plan(self.changed())
        self.assertEqual(align.Counter(j.protocol for j in jobs), dict(opa=54, willow=6, tacita=6))
        self.assertEqual(len(align.full_plan(self.old, self.new, self.c)), 216)
        self.assertEqual(len(self.new.plan(self.c)), 216)

    def test_original_runner_is_unchanged(self):
        self.assertEqual(self.old.VERSION, align.BASE_VERSION)
        self.assertEqual(self.old.Job("opa", 1000, 800, 50, 10000, 1).t, 34)
        self.assertEqual(self.new.Job("opa", 1000, 800, 50, 10000, 1).t, 40)
        self.assertEqual(self.old.Job("willow", 1000, 800, 80, 10000, 1).t, 64)

    def test_commands_control_actual_threshold_and_keep_threads(self):
        for protocol in align.AGG:
            job = self.new.Job(protocol, 1000, 800, 80, 10000, 1)
            cmd, env, cwd = self.new.commands(job, self.c, self.ctx.root / "test_only", self.ctx.root)
            if protocol == "tacita":
                self.assertEqual(cmd[cmd.index("--threshold")+1], "65")
                self.assertEqual(cmd[cmd.index("--threads")+1], "32")
            else:
                self.assertEqual(env["BENCH_THRESHOLD"], "65")
                self.assertEqual(env["BENCH_THREADS"], "32")
            self.assertEqual(env["BENCH_CLIENT_WARMUP"], "3")
            self.assertEqual(env["BENCH_CLIENT_SAMPLES"], "20")
            for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
                self.assertEqual(env[var], "1")

    def test_smoke_covers_every_opa_threshold(self):
        jobs = self.new.plan(self.c, "comparison", True)
        self.assertEqual({(j.m, j.t) for j in jobs}, {(40,32), (50,40), (60,48), (80,65), (100,85)})
        self.assertTrue(all(j.protocol == "opa" and j.n == 20 and j.active == 16 for j in jobs))

    def test_rejects_parameter_drift(self):
        for field, value in (("threads", 16), ("repeats", 1), ("client_samples", 1), ("client_warmups", 0)):
            c = copy.deepcopy(self.c)
            c[field] = value
            with self.assertRaises(ValueError):
                self.new.validate_config(c)

    def test_real_reply_count_is_checked(self):
        job = self.new.Job("opa", 20, 16, 50, 32, 1)
        audit = dict(schema="round-v2", clients=[dict(client_id=i, delivered=i<=16, sent_bytes=100, compute_s=.01)
                                                    for i in range(1,21)],
                     committee_request_bytes=[100]*50, committee_reply_bytes=[10]*40+[0]*10,
                     end_to_end_s=1.2, client_sample_times_s=[.01]*20)
        raw = dict(client_to_server_bytes=2000, active_client_to_server_bytes=1600,
                   server_to_committee_bytes=5000, committee_to_server_bytes=400,
                   end_to_end_s=1.2, client_compute_s=.01)
        self.new.validate_round_audit(audit, raw, job)
        audit["committee_reply_bytes"] = [10]*34 + [0]*16
        raw["committee_to_server_bytes"] = 340
        with self.assertRaisesRegex(ValueError, "exact responder count"):
            self.new.validate_round_audit(audit, raw, job)

    def test_reuse_rejects_different_host_or_binary(self):
        host = dict(host="test-host", cpu_model="test-cpu", available_cpus=32, platform="linux-test",
                    python="3.10.8", source_sha256="test-source", binary_sha256={"opa":"test-bin"})
        baseline = dict(contract=dict(host=copy.deepcopy(host)))
        with patch.object(self.old, "preflight", return_value=host):
            align.install_preflight(self.old, self.new, self.ctx, baseline)
            self.new.preflight(self.c, {}, self.ctx.root)
            for field, value in (("host", "another-host"), ("binary_sha256", {"opa":"new-bin"})):
                changed = copy.deepcopy(host)
                changed[field] = value
                with patch.object(self.old, "preflight", return_value=changed):
                    with self.assertRaises(ValueError):
                        self.new.preflight(self.c, {}, self.ctx.root)

    def test_merged_coverage_rejects_old_opa_and_duplicates(self):
        # Deliberately synthetic unit-test records, only in a TemporaryDirectory.
        jobs = align.full_plan(self.old, self.new, self.c)
        entries = [dict(kind="retest", job=align.asdict(j), threshold=j.t, groups=g, origin_groups=g)
                   for j,g in jobs.items()]
        manifest = dict(schema=align.MERGED, config=self.c, host={"host":"test-only"}, jobs=entries)
        def row(run, job, groups, threads):
            return dict(responding_members=job.t, source="raw/test-only", groups=";".join(groups))
        with tempfile.TemporaryDirectory() as temp, patch.object(self.new, "verify_job", side_effect=row):
            out = align.absolute(temp)
            self.old.write_json(out / "manifest.json", manifest)
            self.assertEqual(len(align.verify_combined(self.old,self.new,out)),216)
            entries.append(entries[0])
            self.old.write_json(out / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "duplicate"):
                align.verify_combined(self.old,self.new,out)
            entries.pop()
            i = next(i for i,e in enumerate(entries) if e["job"]["protocol"] == "opa")
            entries[i] = dict(entries[i], kind="baseline")
            self.old.write_json(out / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "unchanged threshold"):
                align.verify_combined(self.old,self.new,out)

    @unittest.skipUnless(os.environ.get("ALIGN_BASELINE"), "Optional existing-result integrity check")
    def test_existing_216_records_and_copied_evidence(self):
        baseline = align.absolute(os.environ["ALIGN_BASELINE"])
        rows, manifest = align.validate_baseline(self.old, baseline)
        self.assertEqual(len(rows), 216)
        # Round-trip one unchanged measurement of each protocol that can be reused.
        with tempfile.TemporaryDirectory() as temp:
            out = align.absolute(temp)
            for protocol in ("osa", "willow", "tacita"):
                spec = next(j for j in manifest["contract"]["jobs"] if j["protocol"] == protocol and j["m"] == 50)
                job = self.old.Job(**{k:v for k,v in spec.items() if k != "groups"})
                align.evidence_copy(self.old, baseline, out, job)
                with align.recorded_host(manifest["contract"]["host"]["host"]):
                    recovered = self.old.verify_job(out, job, spec["groups"], 32)
                expected = next(r for r in rows if r["job_id"] == job.id)
                self.assertEqual(recovered, expected)


if __name__ == "__main__":
    unittest.main()
