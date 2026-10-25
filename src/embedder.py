"""
Embedding model provider and vector normalization.
"""

from __future__ import annotations

import numpy as np

_MODEL = None
_MODEL_TYPE = None


def get_embedding_model():
    """Load and cache the embedding model."""
    global _MODEL, _MODEL_TYPE
    if _MODEL is None:
        try:
            from sentence_transformers import SentenceTransformer

            _MODEL = SentenceTransformer("all-MiniLM-L6-v2")
            _MODEL_TYPE = "sentence_transformers"
        except ImportError:
            try:
                from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

                _MODEL = DefaultEmbeddingFunction()
                _MODEL_TYPE = "chromadb_onnx"
            except ImportError as exc:
                raise ImportError(
                    "Neither sentence-transformers nor chromadb is installed. "
                    "Please install chromadb: pip install chromadb"
                ) from exc
    return _MODEL


def embed_texts(texts: list[str], batch_size: int = 64) -> np.ndarray:
    """
    Generate L2-normalized embeddings using all-MiniLM-L6-v2.
    L2 normalization guarantees dot product equals cosine similarity.
    """
    if not texts:
        return np.empty((0, 384), dtype=np.float32)

    model = get_embedding_model()

    if hasattr(model, "encode"):
        embeddings = model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return embeddings.astype(np.float32)

    raw_embs = model(texts)
    embs = np.array(raw_embs, dtype=np.float32)
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs = embs / norms
    return embs.astype(np.float32)
