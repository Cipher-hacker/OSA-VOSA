"""
Backward-compatible wrapper for the revised VOSA packed NTT polynomial MSIS protocol.
Delegates all execution and measurements to experiments/vosa.py.
"""

import sys
from pathlib import Path

EXPERIMENTS_DIR = Path(__file__).resolve().parent
if str(EXPERIMENTS_DIR) not in sys.path:
    sys.path.insert(0, str(EXPERIMENTS_DIR))

from vosa import (
    REVISION,
    DEGREE,
    K,
    ELL,
    ELL_PRIME,
    Q,
    THETA,
    ETA,
    L_DEFAULT,
    run_verification_round,
    client_wire_messages,
    server_wire_opening,
    self_test,
    main,
)


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


run_round = run_verification_round

if __name__ == "__main__":
    main()
