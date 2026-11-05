"""Verify the bundled training datasets against the original checksums."""
import gzip
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MNIST_MD5 = {
    "train-images-idx3-ubyte": "f68b3c2dcbeaaa9fbdd348bbdeb94873",
    "train-labels-idx1-ubyte": "d53e105ee54ea40749a09fcbcd1e9432",
    "t10k-images-idx3-ubyte": "9fb629c4189551a2d022fa330f9573f3",
    "t10k-labels-idx1-ubyte": "ec29112dd5afa0611ce80d1b7f02629c",
}
CIFAR_MD5 = {
    "data_batch_1": "c99cafc152244af753f735de768cd75f",
    "data_batch_2": "d4bba439e000b95fd0a9bffe97cbabec",
    "data_batch_3": "54ebc095f3ab1f0389bbae665268c751",
    "data_batch_4": "634d18415352ddfa80567beed471001a",
    "data_batch_5": "482c414d41f54cd18b22e5b47cb7c3cb",
    "test_batch": "40351d587109b95175f43aff81a1287e",
    "batches.meta": "5ff9c542aee3614f3951f8cda6e48888",
}


def file_hash(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_data(root: Path = ROOT) -> dict:
    fingerprints = {}
    raw = root / "datasets/MNIST/raw"
    for name, expected in MNIST_MD5.items():
        packed_path, raw_path = raw / (name + ".gz"), raw / name
        if file_hash(packed_path, "md5") != expected:
            raise RuntimeError(f"MNIST checksum mismatch: {packed_path}")
        if gzip.decompress(packed_path.read_bytes()) != raw_path.read_bytes():
            raise RuntimeError(f"MNIST raw/gzip mismatch: {raw_path}")
        for path in (packed_path, raw_path):
            fingerprints[path.relative_to(root).as_posix()] = file_hash(path)
    for name, expected in CIFAR_MD5.items():
        path = root / "datasets/cifar-10-batches-py" / name
        if file_hash(path, "md5") != expected:
            raise RuntimeError(f"CIFAR-10 checksum mismatch: {path}")
        fingerprints[path.relative_to(root).as_posix()] = file_hash(path)
    # torchvision prefers a legacy processed MNIST cache if it exists.
    if (root / "datasets/MNIST/processed").exists():
        raise RuntimeError("Unexpected MNIST/processed cache; use the clean upload bundle.")
    print("data=PASS: MNIST 60000/10000; CIFAR-10 50000/10000; canonical checksums match", flush=True)
    return fingerprints


