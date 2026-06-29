"""
Image-based detection service using YOLOX + Supervision ByteTrack.

This module powers the ``/api/detectar`` endpoint. It accepts raw image bytes,
runs YOLOX (Apache 2.0) for person detection, tracks with ByteTrack, and
returns bounding boxes plus boarding/alighting events.
"""
from functools import lru_cache

import cv2
import numpy as np
import supervision as sv

from .tracker import actualizar_estado


# ── YOLOX helpers ────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def get_model():
    """Load the YOLOX-S model once and cache it.

    The weights are downloaded automatically from the YOLOX release page
    on first call (``yolox_s.pth``, Apache 2.0 license).
    """
    from yolox.exp import get_exp
    import torch

    exp = get_exp(None, "yolox-s")
    model = exp.get_model()
    model.eval()

    # Download pretrained weights from the official repo
    url = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.pth"
    state_dict = torch.hub.load_state_dict_from_url(url, map_location="cpu", check_hash=False)
    model.load_state_dict(state_dict["model"])
    return model


@lru_cache(maxsize=1)
def get_tracker():
    """Return a shared ByteTrack instance."""
    return sv.ByteTrack(
        track_activation_threshold=0.3,
        lost_track_buffer=30,
        minimum_matching_threshold=0.8,
        frame_rate=30,
    )


def _preprocess(img: np.ndarray, input_size: tuple = (640, 640)) -> np.ndarray:
    """Resize and normalise a frame for YOLOX inference."""
    from yolox.data.data_augment import ValTransform

    transformer = ValTransform(legacy=False)
    image, _ = transformer(img, None, input_size)
    return np.expand_dims(image, axis=0).astype(np.float32)


def _infer(model, image_tensor: np.ndarray, conf: float = 0.5) -> np.ndarray:
    """Run YOLOX inference and return post-processed detections.

    Returns an array of shape ``(N, 6)`` where each row is
    ``[x1, y1, x2, y2, confidence, class_id]``.
    Returns an empty array if no detections pass the threshold.
    """
    from yolox.utils import postprocess
    import torch

    with torch.no_grad():
        outputs = model(torch.from_numpy(image_tensor))

    outputs = postprocess(outputs, num_classes=80, conf_thre=conf, nms_thre=0.5)
    if outputs[0] is None:
        return np.empty((0, 6), dtype=np.float32)
    return outputs[0].cpu().numpy()


# ── public API ───────────────────────────────────────────────────────────────


def detectar_personas(image_bytes: bytes, conf: float = 0.5, iou: float = 0.5) -> dict:
    """Detect people in an image and return bounding boxes + tracking events.

    Args:
        image_bytes: Raw JPEG / PNG bytes.
        conf: Confidence threshold.
        iou: NMS IoU threshold (not used directly; YOLOX applies its own NMS).

    Returns:
        A dict with:

        - **detecciones**: list of ``{x1, y1, x2, y2, cx, cy, conf, track_id}``
        - **eventos**: list of ``{track_id, evento}`` (``subio`` / ``bajo``)
    """
    model = get_model()
    tracker = get_tracker()

    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return {"detecciones": [], "eventos": []}

    h, w = img.shape[:2]

    # ── YOLOX inference ──────────────────────────────────────────────────
    input_blob = _preprocess(img)
    raw_outputs = _infer(model, input_blob, conf=conf)

    detecciones = []
    if len(raw_outputs) > 0:
        # Filter to person class (COCO class 0) and build supervision Detections
        person_mask = raw_outputs[:, 5] == 0
        person_dets = raw_outputs[person_mask]

        if len(person_dets) > 0:
            detections = sv.Detections(
                xyxy=person_dets[:, :4],
                confidence=person_dets[:, 4],
                class_id=person_dets[:, 5].astype(int),
            )

            # Scale boxes from 640x640 back to original image size
            scale_x = w / 640
            scale_y = h / 640
            detections.xyxy[:, [0, 2]] *= scale_x
            detections.xyxy[:, [1, 3]] *= scale_y

            tracks = tracker.update_with_detections(detections)
            for i in range(len(tracks)):
                tid = int(tracks.tracker_id[i])
                box = tracks.xyxy[i]
                x1, y1, x2, y2 = map(float, box)
                conf_val = float(tracks.confidence[i])
                detecciones.append({
                    "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                    "cx": (x1 + x2) / 2,
                    "cy": (y1 + y2) / 2,
                    "conf": conf_val,
                    "track_id": tid,
                })
    else:
        tracker.update_with_detections(sv.Detections.empty())

    eventos = actualizar_estado(detecciones)
    return {"detecciones": detecciones, "eventos": eventos}
