import threading
import numpy as np
from .models import Enrollment

_lock = threading.Lock()
_ids, _matrix = [], np.zeros((0, 128), np.float32)
_loaded = False


def invalidate():
    global _loaded
    _loaded = False


def _ensure():
    global _ids, _matrix, _loaded
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        rows = list(Enrollment.objects.values_list("external_id", "embedding"))
        _ids = [r[0] for r in rows]
        _matrix = (np.stack([np.frombuffer(bytes(r[1]), np.float32) for r in rows])
                   if rows else np.zeros((0, 128), np.float32))
        _loaded = True


def search(vec, k=2):
    _ensure()
    if len(_ids) == 0:
        return []
    scores = _matrix @ vec                      # cosine, since all vectors are normalized
    top = np.argsort(-scores)[:k]
    return [(_ids[i], float(scores[i])) for i in top]