"""
Real-time CLI video detector using YOLOX + ByteTrack + virtual gate.

Run this as a standalone script to open a camera or video file, detect
people, track them across frames, and emit boarding / alighting events
to the NAVO API.

Usage::

    python -m navo_admin.detector.engine --video 0 \\
        --api http://localhost:8000 --conf 0.5
"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
import requests
import supervision as sv

# ── lazy YOLOX imports (heavy, so we defer them) ──────────────────────


def _load_yolox_model(model_name: str = "yolox-s"):
    """Download (if needed) and return a YOLOX model in eval mode."""
    from yolox.exp import get_exp
    import torch

    exp = get_exp(None, model_name)
    model = exp.get_model()
    model.eval()

    url_map = {
        "yolox-s": "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.pth",
        "yolox-m": "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_m.pth",
        "yolox-l": "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_l.pth",
    }
    url = url_map.get(model_name, url_map["yolox-s"])
    state_dict = torch.hub.load_state_dict_from_url(url, map_location="cpu", check_hash=False)
    model.load_state_dict(state_dict["model"])
    return model


def _preprocess(img: np.ndarray, input_size: tuple = (640, 640)) -> np.ndarray:
    """Resize and normalise a frame for YOLOX (letterbox + normalise)."""
    from yolox.data.data_augment import ValTransform

    transformer = ValTransform(legacy=False)
    image, _ = transformer(img, None, input_size)
    return np.expand_dims(image, axis=0).astype(np.float32)


def _infer(model, image_tensor: np.ndarray, conf: float = 0.5) -> np.ndarray:
    """Run YOLOX and return post-processed detections (N, 6)."""
    from yolox.utils import postprocess
    import torch

    with torch.no_grad():
        outputs = model(torch.from_numpy(image_tensor))

    outputs = postprocess(outputs, num_classes=80, conf_thre=conf, nms_thre=0.5)
    if outputs[0] is None:
        return np.empty((0, 6), dtype=np.float32)
    return outputs[0].cpu().numpy()


# ── helpers ──────────────────────────────────────────────────────────────────


CLASES_PERSONA = {0}

API_BASE = "http://localhost:8000"


def dibujar_puerta(frame: np.ndarray, line_y: int) -> None:
    """Draw the virtual gate line on the frame."""
    h, w = frame.shape[:2]
    cv2.line(frame, (0, line_y), (w, line_y), (0, 255, 255), 2)
    cv2.putText(frame, "GATE", (w - 140, line_y - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)


def dibujar_detecciones(frame: np.ndarray, detections: list[dict]) -> None:
    """Draw bounding boxes, IDs, and centroids for each detection."""
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        tid = det["track_id"]
        conf = det["conf"]
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
        label = f"ID:{tid} {conf:.2f}"
        cv2.putText(frame, label, (x1, y1 - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
        cv2.circle(frame, (det["cx"], det["cy"]), 4, (0, 0, 255), -1)


def enviar_evento(direccion: str, track_id: int) -> None:
    """POST a boarding / alighting event to the NAVO API."""
    try:
        if direccion == "sube":
            requests.post(f"{API_BASE}/api/pasajero-subio",
                          json={"track_id": track_id}, timeout=1)
        else:
            requests.post(f"{API_BASE}/api/pasajero-bajo",
                          json={"track_id": track_id}, timeout=1)
    except requests.RequestException as e:
        print(f"[ERROR] Failed to send event: {e}")


# ── main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="navo-admin — YOLOX + Virtual Gate Detector")
    parser.add_argument("--video", type=str, default=None,
                        help="Video source path or '0' for webcam")
    parser.add_argument("--api", type=str, default=API_BASE,
                        help=f"NAVO API base URL (default: {API_BASE})")
    parser.add_argument("--model", type=str, default="yolox-s",
                        choices=("yolox-s", "yolox-m", "yolox-l"),
                        help="YOLOX model size (default: yolox-s)")
    parser.add_argument("--conf", type=float, default=0.5,
                        help="Confidence threshold (default: 0.5)")
    args = parser.parse_args()

    global API_BASE
    API_BASE = args.api

    print(f"[navo-admin] Loading YOLOX model: {args.model}")
    model = _load_yolox_model(args.model)
    tracker = sv.ByteTrack(
        track_activation_threshold=args.conf,
        lost_track_buffer=30,
        minimum_matching_threshold=0.8,
        frame_rate=30,
    )

    video_src: int | str = 0 if args.video == "0" else (args.video or 0)
    cap = cv2.VideoCapture(video_src)
    if not cap.isOpened():
        print("[ERROR] Could not open video source")
        sys.exit(1)

    ret, frame = cap.read()
    if not ret:
        print("[ERROR] Could not read first frame")
        sys.exit(1)

    h, w = frame.shape[:2]

    # ── virtual gate ─────────────────────────────────────────────────────
    from .gate_counter import GateCounter
    gate = GateCounter(frame_height=h)

    requests.post(f"{API_BASE}/api/cambiar-estado-bus",
                  json={"estado": "DETENIDO"}, timeout=1)

    print(f"[navo-admin] Detecting... Gate at Y={gate.line_y} | Press 'q' to quit")
    print("[navo-admin] KEYS: 1=Chinandega  2=Posoltega  3=Leon  0=En ruta  q=quit")

    # ── detection loop ──────────────────────────────────────────────────
    parada_actual = "Chinandega"

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # YOLOX inference
        input_blob = _preprocess(frame)
        raw_outputs = _infer(model, input_blob, conf=args.conf)

        # Filter person class (COCO class 0)
        person_mask = raw_outputs[:, 5] == 0 if len(raw_outputs) > 0 else []
        person_dets = raw_outputs[person_mask] if len(raw_outputs) > 0 else np.empty((0, 6))

        detections = []
        if len(person_dets) > 0:
            # Scale from 640x640 back to original
            scale_x = w / 640
            scale_y = h / 640
            scaled_xyxy = person_dets[:, :4].copy()
            scaled_xyxy[:, [0, 2]] *= scale_x
            scaled_xyxy[:, [1, 3]] *= scale_y

            dets_sv = sv.Detections(
                xyxy=scaled_xyxy,
                confidence=person_dets[:, 4],
                class_id=person_dets[:, 5].astype(int),
            )

            tracks = tracker.update_with_detections(dets_sv)
            for i in range(len(tracks)):
                tid = int(tracks.tracker_id[i])
                box = tracks.xyxy[i]
                x1, y1, x2, y2 = map(int, box)
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                conf_val = float(tracks.confidence[i])
                detections.append({
                    "track_id": tid,
                    "bbox": (x1, y1, x2, y2),
                    "cx": cx,
                    "cy": cy,
                    "conf": conf_val,
                })
        else:
            tracker.update_with_detections(sv.Detections.empty())

        # Gate crossing events
        events = gate.update(detections)
        for ev in events:
            print(f"[EVENT] Person {ev['track_id']} {ev['direccion']}")
            enviar_evento(ev["direccion"], ev["track_id"])

        # Draw overlay
        dibujar_detecciones(frame, detections)
        dibujar_puerta(frame, gate.line_y)

        cv2.putText(frame, f"Stop: {parada_actual}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.putText(frame, f"IDs aboard: {len(detections)}", (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 2)

        cv2.imshow("navo-admin — YOLOX Detector", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('1'):
            parada_actual = "Chinandega"
            requests.post(f"{API_BASE}/api/cambiar-estado-bus",
                          json={"estado": "DETENIDO", "parada": parada_actual}, timeout=1)
        elif key == ord('2'):
            parada_actual = "Posoltega"
            requests.post(f"{API_BASE}/api/cambiar-estado-bus",
                          json={"estado": "DETENIDO", "parada": parada_actual}, timeout=1)
        elif key == ord('3'):
            parada_actual = "Leon"
            requests.post(f"{API_BASE}/api/cambiar-estado-bus",
                          json={"estado": "DETENIDO", "parada": parada_actual}, timeout=1)
        elif key == ord('0'):
            requests.post(f"{API_BASE}/api/cambiar-estado-bus",
                          json={"estado": "EN_RUTA"}, timeout=1)

    requests.post(f"{API_BASE}/api/finalizar-ruta", timeout=1)
    cap.release()
    cv2.destroyAllWindows()
    print("[navo-admin] Program finished")


if __name__ == "__main__":
    main()
