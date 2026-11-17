"""Tests for the revised packed NTT VOSA verification protocol and suite orchestration."""
import subprocess
import sys
from pathlib import Path
import unittest
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments import sis_suite, vosa as vosa_mod, packed as p


class VOSAPackedNTTTests(unittest.TestCase):
    def test_vosa_only_coverage_matches_the_paired_paper_plan(self):
        original = sis_suite.load()
        cfg = original.read_json(original.ROOT / "config.json")
        all_jobs = original.plan(cfg, "verification")
        for cohort, count in (("vosa-matched", 18), ("vosa-grid", 72)):
            suite = sis_suite.load(cohort)
            selected = suite.plan(cfg, "verification")
            expected = {j.id for j, groups in all_jobs.items() if j.protocol == "vosa"
                        and (cohort == "vosa-grid" or "verification_n" in groups)}
            self.assertEqual({j.id for j in selected}, expected)
            self.assertEqual(len(selected), count)
            smoke = suite.plan(cfg, "verification", smoke=True)
            self.assertEqual([j.protocol for j in smoke], ["vosa"])

    def test_ntt_build_and_ring_matrix(self):
        lib, build_info = p.build()
        self.assertIn("compiler", build_info)
        self.assertIn("binary_sha256", build_info)
        a1 = p.Matrix(lib, 2, 1000, 1)
        self.assertEqual(a1.cols, 2)
        self.assertEqual(a1.data.shape[2:], (p.K, p.D))

    def test_wire_serialization_and_exact_byte_counts(self):
        # Test exact serialization for n=100, active=80 matching AutoDL
        n = 100
        active = 80
        theta = vosa_mod.THETA
        eta = vosa_mod.ETA

        r_sample = np.zeros((vosa_mod.ELL, vosa_mod.DEGREE), dtype=np.int32)
        ct_sample = np.zeros((vosa_mod.K, vosa_mod.DEGREE), dtype=object)

        r_msg, ct_msg = vosa_mod.client_wire_messages(1, 0, r_sample, ct_sample, theta)
        # 9 bytes header + 32768 coords * 22 bits / 8 = 9 + 90112 = 90121 bytes
        self.assertEqual(len(r_msg), 90121)
        # 9 bytes header + 32768 coeffs * 24 bytes = 9 + 786432 = 786441 bytes
        self.assertEqual(len(ct_msg), 786441)

        total_client_per_user = len(r_msg) + len(ct_msg)
        self.assertEqual(total_client_per_user, 876562)

        # Server opening frame
        active_ids = list(range(active))
        sum_r = np.zeros((vosa_mod.ELL, vosa_mod.DEGREE), dtype=np.int32)
        sum_x = np.zeros(vosa_mod.L_DEFAULT, dtype=np.int64)
        server_frame = vosa_mod.server_wire_opening(1, n, active_ids, sum_r, sum_x, theta, eta)
        self.assertEqual(len(server_frame), 149710)

        total_system_bytes = active * total_client_per_user + len(server_frame)
        self.assertEqual(total_system_bytes, 70274670)

    def test_run_verification_round_smoke(self):
        # Quick round with 5 clients, 4 active
        res = vosa_mod.run_verification_round(n_clients=5, active_clients=4, vector_len=32, threads=2)
        self.assertEqual(res["protocol"], vosa_mod.REVISION)
        self.assertEqual(res["honest_correctness"], "pass")
        self.assertEqual(res["forged_server_rejection"], "pass")
        self.assertEqual(res["active_client_sent_mean_bytes"], 876562.0)
        self.assertGreater(res["client_compute_mean_s"], 0)
        self.assertGreater(res["server_compute_s"], 0)
        self.assertGreater(res["verifier_compute_s"], 0)

    def test_cli_execution_and_suite_parse(self):
        cmd = [sys.executable, "-u", str(ROOT / "experiments/vosa.py"),
               "--clients", "5", "--active", "4", "--length", "32", "--threads", "2"]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, cwd=ROOT, check=True)
        suite = sis_suite.load()
        parsed = suite.parse_line(proc.stdout)
        self.assertEqual(parsed["protocol"], vosa_mod.REVISION)
        self.assertEqual(parsed["honest_correctness"], "pass")
        self.assertEqual(parsed["forged_server_rejection"], "pass")
        self.assertEqual(float(parsed["active_client_sent_mean_bytes"]), 876562.0)


if __name__ == "__main__":
    unittest.main()
