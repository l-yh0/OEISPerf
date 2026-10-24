"""Loader for the OEISPerf dataset on the Hugging Face Hub.

The reference solutions and the ground-truth terms are stored encrypted on the
Hub; ``OEISPerfLoader.load`` decrypts them in memory.

Encrypted field format (``v1``)::

    "v1:" + base64(nonce[12] || AES-256-GCM(ciphertext || tag))

    plaintext = zstd(utf-8 text)           # ``expected_output`` is JSON text
    key       = SHA-256(b"OEISPerf")
    AAD       = JSON(sorted keys, no spaces) of
                {"dataset", "field", "format", "split", "task_id"}

The AAD ties every ciphertext to its split, task id and field, so ciphertexts
cannot be swapped between rows or splits.
"""

import base64
import hashlib
import json

import pandas as pd
import zstandard as zstd
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from datasets import load_dataset


class OEISPerfLoader:
    DATASET = "l-yh/OEISPerf"
    SPLITS = ("train", "validation", "test")
    KEY = hashlib.sha256(b"OEISPerf").digest()

    @classmethod
    def _decrypt(cls, x: str, split: str, task_id: str, field: str) -> str:
        version, payload = x.split(":", 1)
        if version != "v1":
            raise ValueError(
                f"Unsupported encryption format {version!r} for {field} of {task_id}.")

        raw = base64.b64decode(payload)

        aad = json.dumps({
            "dataset": "OEISPerf",
            "field": field,
            "format": "v1",
            "split": split,
            "task_id": str(task_id),
        }, sort_keys=True, separators=(",", ":")).encode()

        try:
            data = AESGCM(cls.KEY).decrypt(raw[:12], raw[12:], aad)
        except InvalidTag as exc:
            raise ValueError(
                f"Cannot decrypt {field} of {task_id} (split={split!r}): "
                "the ciphertext is corrupted or does not belong to this split/id."
            ) from exc
        return zstd.ZstdDecompressor().decompress(data).decode()

    @classmethod
    def load(cls, split: str = "test") -> pd.DataFrame:
        """Download one split and decrypt it in memory.

        Returns a DataFrame with the columns ``id``, ``inefficientCode``,
        ``offset_start``, ``termSize``, plus the decrypted ``referenceCode``
        (str) and ``expected_output`` (list of decimal strings).
        """
        if split not in cls.SPLITS:
            raise ValueError(
                f"Unknown split {split!r}; expected one of {cls.SPLITS}.")

        df = load_dataset(cls.DATASET, split=split).to_pandas()

        df["referenceCode"] = [
            cls._decrypt(x, split, task_id, "referenceCode")
            for x, task_id in zip(df["encrypted_referenceCode"], df["id"])
        ]
        df["expected_output"] = [
            json.loads(cls._decrypt(x, split, task_id, "expected_output"))
            for x, task_id in zip(df["encrypted_expected_output"], df["id"])
        ]

        return df.drop(columns=[
            "encrypted_referenceCode",
            "encrypted_expected_output",
        ])
