"""OSNet as a BoT-SORT ReID encoder.

Ultralytics' tracker ReID with ``model: auto`` reuses the *detector's* backbone
features as the appearance descriptor — not trained for re-identification, so
weak at "is this the same person" (it failed to re-link fragments on real
footage). This swaps in **OSNet**, a model trained specifically for person
re-ID — the *same* ONNX the body-ID pipeline uses, so the tracker's notion of
"same person" agrees with the identity layer's.

It matches Ultralytics' encoder contract — a callable
``(img_BGR, dets[:, :4]=xywh_pixels) -> list[np.ndarray]`` of L2-normalized
embeddings, one per detection — and :func:`install` monkeypatches it into the
BoT-SORT tracker (use a tracker config with ``with_reid: true`` and a non-
``auto`` ``model`` so Ultralytics takes the ReID-encoder path).
"""

from __future__ import annotations

import numpy as np

from kukiihome_preprocessor.pipelines.body_id import _l2_normalize_rows, _preprocess


class OSNetReID:
    """Callable OSNet appearance encoder for BoT-SORT."""

    def __init__(self, model: str, *, height: int = 256, width: int = 128) -> None:
        import onnxruntime as ort

        self._sess = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
        self._input = self._sess.get_inputs()[0].name
        self._h, self._w = height, width

    def __call__(self, img: np.ndarray, dets: np.ndarray) -> list[np.ndarray]:
        h, w = img.shape[:2]
        crops: list[np.ndarray] = []
        for d in dets:
            cx, cy, bw, bh = float(d[0]), float(d[1]), float(d[2]), float(d[3])
            x1, y1 = max(0, int(cx - bw / 2)), max(0, int(cy - bh / 2))
            x2, y2 = min(w, int(cx + bw / 2)), min(h, int(cy + bh / 2))
            if x2 <= x1 or y2 <= y1:
                crops.append(np.zeros((3, self._h, self._w), dtype=np.float32))
                continue
            crops.append(_preprocess(img[y1:y2, x1:x2], self._h, self._w))
        if not crops:
            return []
        batch = np.stack(crops).astype(np.float32)
        out = np.asarray(self._sess.run(None, {self._input: batch})[0], dtype=np.float32)
        normed = _l2_normalize_rows(out)
        return [normed[i] for i in range(normed.shape[0])]


def install(onnx_path: str) -> None:
    """Monkeypatch Ultralytics' ``bot_sort.ReID`` to use OSNet at ``onnx_path``,
    ignoring the tracker config's ``model`` string. Call once before
    ``model.track``; pair with a tracker config that has ``with_reid: true`` +
    a non-``auto`` ``model`` (so the encoder-construction path runs)."""
    import ultralytics.trackers.bot_sort as bs

    class _BoundOSNetReID(OSNetReID):
        def __init__(self, model: str) -> None:  # noqa: ARG002 — yaml's model str ignored
            super().__init__(onnx_path)

    bs.ReID = _BoundOSNetReID
