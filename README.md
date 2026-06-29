# navo-admin

> Open-source bus route auditing, passenger counting, and fraud detection system.

navo-admin helps public transport companies monitor passenger flow, detect revenue leakage, and optimise route operations using computer vision and GPS tracking.

**License:** Apache 2.0 — free to use, modify, and distribute.

---

## Features

- **Real-time passenger counting** — YOLOX-based person detection with virtual gate crossing
- **Fraud detection** — automatic alerts for same-stop boarding/alighting, unauthorised crew, overcapacity, and overweight
- **Route management** — create and manage routes, stops, and fare tables (one file per route)
- **GPS integration** — Haversine-based stop detection with configurable radius
- **Bus assignment** — assign buses to routes and track active units
- **Live dashboard** — web UI with real-time metrics, camera feed, and alerts
- **Simulation mode** — auto-generate passengers for demos and testing
- **REST API** — FastAPI backend with full CRUD for routes and assignments
- **Extensible** — pip-installable Python package, designed to be extended by enterprise editions

## Quick start

```bash
cd backend
pip install -e .
uvicorn app:app --reload
```

Open http://localhost:8000

## Tech stack

- **Backend:** Python, FastAPI, YOLOX, Supervision, ONNX Runtime
- **Frontend:** HTML, CSS, JavaScript (vanilla)
- **Data:** JSON file storage (one file per route)
- **Vision:** YOLOX object detection + ByteTrack + virtual gate counter

## License

Apache 2.0 — see [LICENSE](LICENSE).
