import json
import hashlib
from typing import Any, Dict

class EvidenceBundle:
    def __init__(self, data: Dict[str, Any]):
        self.data = data

    def serialize(self) -> bytes:
        """
        Produce a deterministic JSON serialization.
        """
        return json.dumps(self.data, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def compute_hash(self) -> str:
        """
        Compute a SHA256 hash of the deterministic serialization.
        """
        serialized = self.serialize()
        return hashlib.sha256(serialized).hexdigest()

    def is_tampered(self, expected_hash: str) -> bool:
        return self.compute_hash() != expected_hash
