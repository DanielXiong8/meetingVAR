"""Local Chinese embedding provider (BGE small) with safe fallbacks.

The model is optional.  When ``bge-small-zh-v1.5`` is present under
``MEETINGVAR_MODELS`` (or the repo runtime), memory/RAG use dense retrieval;
otherwise they keep working with the FTS5 lexical path.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from .. import config

_MODEL_LOCK = threading.RLock()
_FOUND = None
_FOUND_READY = False
_GLOBAL_PROVIDER = None


def _candidate_paths() -> list[Path]:
    paths = []
    configured = os.environ.get("MEETINGVAR_EMBEDDING_MODEL", "").strip()
    if configured:
        paths.append(Path(configured).expanduser())
    roots = [config.MODELS_ROOT, Path(__file__).resolve().parents[3] / "runtime" / "models"]
    for root in roots:
        if not root.is_dir():
            continue
        for cfg in root.rglob("config.json"):
            marker = str(cfg).casefold()
            if any(name in marker for name in ("bge-small-zh", "bge-base-zh", "bge-m3")):
                paths.append(cfg.parent)
    return paths


def find_model() -> Path | None:
    global _FOUND, _FOUND_READY
    if _FOUND_READY:
        return _FOUND
    _FOUND_READY = True
    for path in _candidate_paths():
        if path.is_file():
            _FOUND = path.parent
            return _FOUND
        if (path / "config.json").is_file():
            _FOUND = path
            return _FOUND
    return None


def get_embedding_provider() -> "EmbeddingProvider":
    global _GLOBAL_PROVIDER
    with _MODEL_LOCK:
        if _GLOBAL_PROVIDER is None:
            _GLOBAL_PROVIDER = EmbeddingProvider()
        return _GLOBAL_PROVIDER


class EmbeddingProvider:
    def __init__(self, model_path: str | Path | None = None):
        self._model_path = Path(model_path) if model_path else None
        self._tokenizer = None
        self._model = None
        self._failed = False
        self.name = ""

    @property
    def path(self) -> Path | None:
        if self._model_path is None:
            self._model_path = find_model()
        return self._model_path

    @property
    def available(self) -> bool:
        return self.path is not None and not self._failed

    def _load(self) -> bool:
        if self._model is not None:
            return True
        if self._failed or self.path is None:
            return False
        with _MODEL_LOCK:
            if self._model is not None:
                return True
            try:
                import torch
                from transformers import AutoModel, AutoTokenizer

                path = str(self.path)
                self._tokenizer = AutoTokenizer.from_pretrained(path)
                # The safetensors loader on this Python/transformers combination
                # can raise MemoryError for a 95 MB model; the .bin path is stable.
                self._model = AutoModel.from_pretrained(
                    path, use_safetensors=False, low_cpu_mem_usage=False)
                self._model.eval()
                marker = str(self.path).casefold()
                for name in ("bge-small-zh-v1.5", "bge-base-zh-v1.5", "bge-m3"):
                    if name in marker:
                        self.name = name
                        break
                else:
                    self.name = self.path.name
                return True
            except Exception:
                self._failed = True
                self._tokenizer = None
                self._model = None
                return False

    def encode(self, texts: list[str], batch_size: int = 16) -> list[list[float]] | None:
        if not texts or not self._load():
            return None
        try:
            import torch

            rows: list[list[float]] = []
            with torch.no_grad():
                for start in range(0, len(texts), max(1, batch_size)):
                    batch = [str(text)[:4000] for text in texts[start:start + batch_size]]
                    encoded = self._tokenizer(batch, padding=True, truncation=True,
                                              max_length=512, return_tensors="pt")
                    output = self._model(**encoded).last_hidden_state
                    mask = encoded["attention_mask"].unsqueeze(-1).float()
                    vectors = (output * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
                    vectors = torch.nn.functional.normalize(vectors, p=2, dim=1)
                    rows.extend(vector.tolist() for vector in vectors)
            return rows
        except Exception:
            self._failed = True
            return None

    def encode_one(self, text: str) -> list[float] | None:
        rows = self.encode([text])
        return rows[0] if rows else None