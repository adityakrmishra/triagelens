"""
src/embedder.py
---------------
Singleton loader for the all-MiniLM-L6-v2 embedding model.

Both the AST indexer and the ticket clusterer call `get_embedding_model()`
so the same model instance is reused across the process -- no double-loading.

Attempts SentenceTransformer first; if not installed or unavailable,
seamlessly falls back to ChromaDB's built-in DefaultEmbeddingFunction()
(which runs the exact same all-MiniLM-L6-v2 model locally via ONNX).
"""

from __future__ import annotations

import numpy as np

# Module-level cache: None until first call to get_embedding_model()
_MODEL = None
_MODEL_TYPE = None


def get_embedding_model():
    """
    Load and cache the embedding model (all-MiniLM-L6-v2).

    Tries SentenceTransformer first; if not installed, falls back to
    ChromaDB's DefaultEmbeddingFunction (local ONNX all-MiniLM-L6-v2).

    Returns:
        SentenceTransformer or DefaultEmbeddingFunction instance.
    """
    global _MODEL, _MODEL_TYPE
    if _MODEL is None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            print("[INFO] Loading SentenceTransformer model: all-MiniLM-L6-v2")
            _MODEL = SentenceTransformer("all-MiniLM-L6-v2")
            _MODEL_TYPE = "sentence_transformers"
            print("[OK]   SentenceTransformer model loaded and cached.")
        except ImportError:
            print("[INFO] sentence-transformers not found. Falling back to ChromaDB DefaultEmbeddingFunction (ONNX all-MiniLM-L6-v2)...")
            try:
                from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

                _MODEL = DefaultEmbeddingFunction()
                _MODEL_TYPE = "chromadb_onnx"
                print("[OK]   ChromaDB DefaultEmbeddingFunction loaded and cached.")
            except ImportError as exc:
                raise ImportError(
                    "Neither sentence-transformers nor chromadb is installed. "
                    "Please install chromadb: pip install chromadb"
                ) from exc
    return _MODEL


def embed_texts(texts: list[str], batch_size: int = 64) -> np.ndarray:
    """
    Embed a list of strings using the shared all-MiniLM-L6-v2 model.

    Embeddings are L2-normalised so that dot product equals cosine similarity
    (both in [-1, 1]; for these non-negative embeddings effectively in [0, 1]).

    Args:
        texts:      List of strings to embed.
        batch_size: Mini-batch size passed to the transformer (default 64).

    Returns:
        np.ndarray of shape (len(texts), 384), dtype float32, unit-normalised.
    """
    if not texts:
        return np.empty((0, 384), dtype=np.float32)

    model = get_embedding_model()

    if hasattr(model, "encode"):
        embeddings = model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,   # dot product == cosine similarity
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return embeddings.astype(np.float32)

    # ChromaDB DefaultEmbeddingFunction
    raw_embs = model(texts)
    embs = np.array(raw_embs, dtype=np.float32)
    # L2-normalize
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs = embs / norms
    return embs.astype(np.float32)
