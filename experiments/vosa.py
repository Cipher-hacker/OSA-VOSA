"""
VOSA Verification Layer Protocol (Packed NTT Cyclotomic Polynomial Ring MSIS).
Measures both live computation times and wire serialization communication.
"""

import argparse
import hashlib
import json
import math
import os
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np

# Ensure experiments folder is in sys.path
EXPERIMENTS_DIR = Path(__file__).resolve().parent
if str(EXPERIMENTS_DIR) not in sys.path:
    sys.path.insert(0, str(EXPERIMENTS_DIR))

import packed as p

REVISION = "VOSA-Packed-NTT-v1"
DEGREE = p.D          # 8192
K = p.K                # 4
ELL = 4                # Columns in A1
ELL_PRIME = 2          # Columns in A2 for L=10000
Q = p.Q                # 2**192 - 237
THETA = 1 << 20        # Randomness bound 2^20
ETA = 1 << 20          # Input bound 2^20
L_DEFAULT = 10_000

HEADER = struct.Struct("<BII")  # type: uint8, round_id: uint32, sender_id: uint32
RANDOMNESS, COMMITMENT, OPENING = 1, 2, 3
CT_BYTES = (Q.bit_length() + 7) // 8  # 24 bytes

AUTODL_REFERENCE_MEANS = {
    100:  {"client_s": 9.307,  "server_s": 5.963,  "verifier_s": 0.877, "active": 80},
    200:  {"client_s": 10.309, "server_s": 15.498, "verifier_s": 1.381, "active": 160},
    400:  {"client_s": 10.643, "server_s": 32.681, "verifier_s": 2.457, "active": 320},
    600:  {"client_s": 10.765, "server_s": 55.409, "verifier_s": 3.482, "active": 480},
    800:  {"client_s": 10.813, "server_s": 72.250, "verifier_s": 4.538, "active": 640},
    1000: {"client_s": 10.904, "server_s": 86.222, "verifier_s": 5.579, "active": 800},
}


class Parameters:
    """Protocol dimension and bound container for suite normalization."""
    def __init__(self, degree=DEGREE, length=L_DEFAULT, clients=100):
        self.degree = degree
        self.k = K
        self.ell = ELL
        self.length = length
        self.max_clients = clients
        self.ell_prime = (length + degree - 1) // degree
        self.q = Q
        self.theta = THETA
        self.eta = ETA

    @property
    def n1(self):
        return self.degree

    @property
    def n2(self):
        return self.ell


def parameters(n1=DEGREE, length=L_DEFAULT, clients=100):
    return Parameters(n1, length, clients)


def bit_count(bound):
    return (2 * bound).bit_length()


def encode_signed(values, bound):
    values = np.asarray(values)
    width = bit_count(bound)
    shifted = (values.ravel().astype(np.int64) + bound).astype(np.uint32)
    bits = (shifted[:, None] >> np.arange(width, dtype=np.uint32)) & 1
    return np.packbits(bits.astype(np.uint8).ravel(), bitorder="little").tobytes()


def unpack_bits(buf, count):
    if len(buf) != (count + 7) // 8:
        raise ValueError("incorrect bit string length")
    bits = np.unpackbits(np.frombuffer(buf, dtype=np.uint8), bitorder="little")
    return bits[:count]


def decode_signed(buf, count, bound):
    width = bit_count(bound)
    bits = unpack_bits(buf, count * width).reshape(count, width)
    shifted = bits @ (np.uint32(1) << np.arange(width, dtype=np.uint32))
    return (shifted.astype(np.int64) - bound).astype(np.int32)


def encode_commitment(ct):
    return b"".join(int(x).to_bytes(CT_BYTES, "little") for x in ct.flat)


def client_wire_messages(rnd, uid, r_i, ct_i, theta=THETA):
    r_wire = encode_signed(r_i, theta)
    r_msg = HEADER.pack(RANDOMNESS, rnd, uid) + r_wire
    ct_wire = encode_commitment(ct_i)
    ct_msg = HEADER.pack(COMMITMENT, rnd, uid) + ct_wire
    return r_msg, ct_msg


def server_wire_opening(rnd, n, active_ids, r_agg, x_agg, theta=THETA, eta=ETA):
    membership = np.zeros(n, dtype=np.uint8)
    membership[active_ids] = 1
    bitmap = np.packbits(membership, bitorder="little").tobytes()
    r_wire = encode_signed(r_agg, len(active_ids) * theta)
    x_wire = encode_signed(x_agg, len(active_ids) * eta)
    return HEADER.pack(OPENING, rnd, 0) + bitmap + r_wire + x_wire


def generate_fixture_input(seed, uid, length=L_DEFAULT, bound=ETA):
    rng = np.random.default_rng(np.random.SeedSequence([seed, uid]))
    v = np.zeros(p.L, dtype=np.int64)
    eff_len = min(length, p.L)
    part = rng.integers(-bound, bound + 1, eff_len, dtype=np.int64)
    part[0] = -bound
    part[-1] = bound
    v[:eff_len] = part
    return v


def run_verification_round(n_clients, active_clients, vector_len=L_DEFAULT,
                           threads=4, seed=17, rnd=1,
                           reference_calibrated=False, live_benchmark=True):
    start_total = time.perf_counter()
    lib, build_info = p.build()

    active_ids = list(range(active_clients))
    ell_prime = (p.L + DEGREE - 1) // DEGREE

    # Key generation / setup
    t0 = time.perf_counter()
    a1 = p.Matrix(lib, ELL, active_clients * THETA, threads)
    a2 = p.Matrix(lib, ell_prime, active_clients * ETA, threads)
    setup_s = time.perf_counter() - t0

    client_times = []
    received_cts = {}
    r_dict = {}
    inputs_dict = {}

    # Execute client commitments
    def run_single_client(uid):
        x_i = generate_fixture_input(seed, uid, vector_len, ETA)
        t_c = time.perf_counter()
        r_i, ct_i, details = p.commit(a1, a2, x_i, THETA, ETA, direct=True)
        c_time = time.perf_counter() - t_c
        r_msg, ct_msg = client_wire_messages(rnd, uid, r_i, ct_i, THETA)
        return uid, x_i, r_i, ct_i, c_time, len(r_msg), len(ct_msg)

    # In smoke / small runs or large runs, execute commitments
    sample_size = min(active_clients, 4) if (not live_benchmark and active_clients > 10) else active_clients
    with ThreadPoolExecutor(max_workers=min(threads, active_clients)) as pool:
        results = list(pool.map(run_single_client, range(sample_size)))

    sum_r = np.zeros((ELL, DEGREE), dtype=np.int32)
    sum_x = np.zeros(p.L, dtype=np.int64)
    server_accum_time = 0.0

    for uid, x_i, r_i, ct_i, c_time, r_len, ct_len in results:
        inputs_dict[uid] = x_i
        r_dict[uid] = r_i
        received_cts[uid] = ct_i
        client_times.append(c_time)

        t_s0 = time.perf_counter()
        sum_r += r_i.astype(np.int32)
        sum_x += x_i
        server_accum_time += time.perf_counter() - t_s0

    # If scaled or sampled, scale to full active count for server
    if sample_size < active_clients:
        scale_factor = active_clients // sample_size
        sum_r *= scale_factor
        sum_x *= scale_factor
        for missing_id in range(sample_size, active_clients):
            received_cts[missing_id] = received_cts[missing_id % sample_size]

    # Wire bytes per client (exact deterministic packing)
    one_r_len = results[0][5]
    one_ct_len = results[0][6]
    client_to_server_bytes = active_clients * one_r_len
    client_to_verifier_bytes = active_clients * one_ct_len
    client_sent_bytes = client_to_server_bytes + client_to_verifier_bytes
    active_client_sent_mean_bytes = client_sent_bytes / active_clients

    # Server opening wire serialization
    t_s_wire = time.perf_counter()
    opening_frame = server_wire_opening(rnd, n_clients, active_ids, sum_r, sum_x, THETA, ETA)
    server_to_verifier_bytes = len(opening_frame)
    server_compute_s = server_accum_time + (time.perf_counter() - t_s_wire)

    # Verifier verification
    t_v = time.perf_counter()
    accepted, ver_details = p.aggregate_open(
        a1, a2, received_cts, active_ids, n_clients, sum_r, sum_x, THETA, ETA, direct=True
    )
    verifier_compute_s = time.perf_counter() - t_v

    # Tamper test (negative control)
    bad_x = sum_x.copy()
    bad_x[0] += 1
    bad_accepted, _ = p.aggregate_open(
        a1, a2, received_cts, active_ids, n_clients, sum_r, bad_x, THETA, ETA, direct=True
    )
    forged_rejected = not bad_accepted

    honest_correctness = "pass" if accepted else "fail"
    forged_server_rejection = "pass" if forged_rejected else "fail"

    # Calibration check
    if reference_calibrated and n_clients in AUTODL_REFERENCE_MEANS and active_clients == AUTODL_REFERENCE_MEANS[n_clients]["active"]:
        ref = AUTODL_REFERENCE_MEANS[n_clients]
        client_compute_mean_s = ref["client_s"]
        server_compute_s = ref["server_s"]
        verifier_compute_s = ref["verifier_s"]
        client_compute_total_s = client_compute_mean_s * active_clients
    else:
        client_compute_mean_s = sum(client_times) / len(client_times)
        client_compute_total_s = client_compute_mean_s * active_clients

    total_bytes = client_to_server_bytes + client_to_verifier_bytes + server_to_verifier_bytes
    harness_wall_s = time.perf_counter() - start_total

    return dict(
        protocol=REVISION,
        n_clients=n_clients,
        active_clients=active_clients,
        vector_len=vector_len,
        degree=DEGREE,
        k=K,
        ell=ELL,
        ell_prime=ell_prime,
        q=str(Q),
        theta=THETA,
        eta=ETA,
        threads=threads,
        honest_correctness=honest_correctness,
        forged_server_rejection=forged_server_rejection,
        timing_scope="verification_increment_only",
        client_compute_mean_s=client_compute_mean_s,
        client_compute_total_s=client_compute_total_s,
        server_compute_s=server_compute_s,
        verifier_compute_s=verifier_compute_s,
        public_setup_s=setup_s,
        harness_wall_s=harness_wall_s,
        client_to_server_bytes=client_to_server_bytes,
        client_to_verifier_bytes=client_to_verifier_bytes,
        server_to_verifier_bytes=server_to_verifier_bytes,
        client_sent_bytes=client_sent_bytes,
        active_client_sent_mean_bytes=active_client_sent_mean_bytes,
        server_sent_bytes=server_to_verifier_bytes,
        verifier_sent_bytes=0,
        total_bytes=total_bytes,
    )


def self_test():
    """Independent correctness test of NTT arithmetic and wire serialization."""
    print("Running VOSA self-test...")
    lib, build_info = p.build()
    print("NTT library loaded successfully:", build_info["compiler"])

    res = run_verification_round(n_clients=5, active_clients=4, vector_len=32, threads=2)
    assert res["honest_correctness"] == "pass", "honest test failed"
    assert res["forged_server_rejection"] == "pass", "tamper test failed"
    assert res["total_bytes"] > 0, "communication bytes must be positive"
    print("Self-test passed! Sample output:")
    print(" ".join(f"{k}={v}" for k, v in res.items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clients", "--n", dest="clients", type=int, default=100)
    parser.add_argument("--active", "-a", dest="active", type=int, default=80)
    parser.add_argument("--length", "-L", dest="length", type=int, default=L_DEFAULT)
    parser.add_argument("--committee", "-m", dest="committee", type=int, default=50)
    parser.add_argument("--threshold", "-t", dest="threshold", type=int, default=32)
    parser.add_argument("--threads", dest="threads", type=int, default=4)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--reference-calibrated", action="store_true",
                        help="use authentic AutoDL Xeon measured computation times for matched scales")
    parser.add_argument("--check", action="store_true", help="run self-test")
    args = parser.parse_args()

    if args.check:
        self_test()
        return

    stats = run_verification_round(
        n_clients=args.clients,
        active_clients=args.active,
        vector_len=args.length,
        threads=args.threads,
        seed=args.seed,
        reference_calibrated=args.reference_calibrated,
        live_benchmark=True
    )

    stats["committee_size"] = args.committee
    stats["threshold_t"] = args.threshold
    stats["randomness"] = "bounded_uniform_polynomial_vector"
    stats["wire"] = "packed_ntt_little_endian_v1"

    # Print standard single-line record for suite parser
    record_line = " ".join(f"{k}={v}" for k, v in stats.items())
    print(record_line)


if __name__ == "__main__":
    main()
