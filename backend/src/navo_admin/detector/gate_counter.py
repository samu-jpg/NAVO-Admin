"""
Virtual-gate counter that detects when a tracked person crosses a horizontal
line in the video frame.

Used by the real-time CLI application to detect boarding / alighting events
without relying on disappearance from the frame.
"""
import time
from collections import defaultdict

LINE_Y_RATIO = 0.6
ABOVE = "arriba"
BELOW = "abajo"


class GateCounter:
    """Detect people crossing a virtual horizontal gate line.

    Args:
        frame_height: Video frame height in pixels (used to compute line_y).
        line_y: Fixed Y coordinate for the gate. Overrides frame_height.
    """

    def __init__(self, frame_height: int | None = None, line_y: int | None = None):
        self.frame_height = frame_height
        self.line_y = line_y or (int(frame_height * LINE_Y_RATIO) if frame_height else None)
        self.prev_positions: dict[int, dict] = {}
        self.last_event_time: dict[int, float] = {}
        self.cooldown = 1.5

    def set_frame_size(self, height: int) -> None:
        """Update the gate line position after a frame size change."""
        self.frame_height = height
        self.line_y = int(height * LINE_Y_RATIO)

    def _get_side(self, y: int) -> str:
        return BELOW if y > self.line_y else ABOVE

    def update(self, detections: list[dict]) -> list[dict]:
        """Process a new frame's detections and return crossing events.

        Each event dict contains ``track_id``, ``direccion`` (``"sube"`` /
        ``"baja"``), and ``timestamp``.
        """
        events = []
        current_ids = set()
        now = time.time()

        for det in detections:
            track_id = det["track_id"]
            cx, cy = det["cx"], det["cy"]
            current_ids.add(track_id)

            if track_id in self.prev_positions:
                prev_side = self._get_side(self.prev_positions[track_id]["cy"])
                curr_side = self._get_side(cy)

                if prev_side != curr_side:
                    last = self.last_event_time.get(track_id, 0)
                    if now - last > self.cooldown:
                        direction = "sube" if curr_side == BELOW else "baja"
                        events.append({
                            "track_id": track_id,
                            "direccion": direction,
                            "timestamp": now,
                        })
                        self.last_event_time[track_id] = now

            self.prev_positions[track_id] = {"cx": cx, "cy": cy}

        for tid in list(self.prev_positions):
            if tid not in current_ids:
                del self.prev_positions[tid]

        return events
