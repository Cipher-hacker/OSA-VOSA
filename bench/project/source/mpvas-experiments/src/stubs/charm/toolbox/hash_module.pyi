from typing import Any

from charm.toolbox.pairinggroup import PairingGroup

class Hash:
    def __init__(self, pairingElement: PairingGroup) -> None: ...
    def hashToZr(self, *args: Any) -> int: ...
