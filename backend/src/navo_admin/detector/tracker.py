"""
Simple inter-frame tracker that logs when tracked objects appear / disappear.

Used by the image-based API endpoint to turn raw detections into
boarding / alighting events.
"""

TRACKS: dict[str, dict] = {}
_prev_frame_ids: set[int] = set()


def actualizar_estado(detecciones: list[dict]) -> list[dict]:
    """Compare current detections against the previous frame and emit events.

    Returns a list of event dicts with keys ``track_id`` and ``evento``
    (either ``"subio"`` or ``"bajo"``).
    """
    global _prev_frame_ids
    current_ids = {d["track_id"] for d in detecciones if d.get("track_id") is not None}
    eventos = []

    for n in detecciones:
        tid = n.get("track_id")
        if tid and tid not in _prev_frame_ids:
            eventos.append({"track_id": tid, "evento": "subio"})

    for tid in _prev_frame_ids:
        if tid not in current_ids:
            eventos.append({"track_id": tid, "evento": "bajo"})

    for d in detecciones:
        tid = d.get("track_id")
        if tid is not None:
            TRACKS[str(tid)] = {
                "box": (d["x1"], d["y1"], d["x2"], d["y2"]),
                "cent": (d["cx"], d["cy"]),
                "lost": 0,
            }

    for tid_str in list(TRACKS.keys()):
        tid = int(tid_str)
        if tid not in current_ids:
            TRACKS[tid_str]["lost"] += 1
            if TRACKS[tid_str]["lost"] >= 30:
                del TRACKS[tid_str]

    _prev_frame_ids = current_ids
    return eventos


def reset_tracks() -> None:
    """Clear all tracked IDs and frame history."""
    global TRACKS, _prev_frame_ids
    TRACKS = {}
    _prev_frame_ids = set()
