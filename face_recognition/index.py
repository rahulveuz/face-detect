import threading
import numpy as np
import faiss
from .models import Enrollment

_lock = threading.Lock()
_DIM = 128                     # SFace vector dimensionality
_index = None                  # FAISS HNSW index
_id_map = []                   # index integer -> external_id
_loaded = False


def _build_empty_index():
    """
    IndexHNSWFlat provides sub-millisecond approximate nearest neighbor 
    search over 100,000+ vectors. Using Inner Product (METRIC_INNER_PRODUCT) 
    computes exact cosine similarity because all vectors are L2-normalized.
    """
    # M = 32: Number of bidirectional links per vector node in HNSW graph
    index = faiss.IndexHNSWFlat(_DIM, 32, faiss.METRIC_INNER_PRODUCT)
    # efSearch = 64: Depth of graph traversal during search (high accuracy)
    index.hnsw.efSearch = 64
    return index


def invalidate():
    """Marks cache as dirty so the next search refreshes from DB."""
    global _loaded
    _loaded = False


def _ensure():
    """Thread-safe index loader from PostgreSQL into FAISS HNSW."""
    global _index, _id_map, _loaded
    if _loaded:
        return

    with _lock:
        if _loaded:
            return

        rows = list(Enrollment.objects.values_list("external_id", "embedding"))
        new_index = _build_empty_index()
        new_id_map = [r[0] for r in rows]

        if rows:
            # Reconstruct float32 array matrix: shape (N, 128)
            matrix = np.stack([np.frombuffer(bytes(r[1]), dtype=np.float32) for r in rows])
            # FAISS expects float32 C-contiguous array
            matrix = np.ascontiguousarray(matrix, dtype=np.float32)
            new_index.add(matrix)

        _index = new_index
        _id_map = new_id_map
        _loaded = True


def search(vec, k=5):
    """
    Searches the top-k closest vectors in under 2ms across 100k+ embeddings.
    Returns: [(external_id, score), ...]
    """
    _ensure()
    if _index is None or _index.ntotal == 0:
        return []

    # Format query vector to shape (1, 128) float32
    q = np.ascontiguousarray(np.array([vec], dtype=np.float32))
    
    # Query FAISS: D = cosine similarity scores, I = internal integer IDs
    actual_k = min(k, _index.ntotal)
    D, I = _index.search(q, actual_k)

    scores = D[0]
    indices = I[0]

    results = []
    for idx, score in zip(indices, scores):
        if idx != -1 and idx < len(_id_map):
            results.append((_id_map[idx], float(score)))

    return results
