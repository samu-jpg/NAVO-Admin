"""
Shared in-memory state for the running bus session.

Holds bus status, passenger registry, travel history, and simulation state.
These structures are reset on server restart.
"""
import threading
from datetime import datetime, timezone
from ..detector.fraud_detector import (
    PASAJEROS_ABORDO, CARGA_ABORDO,
    PESO_PROMEDIO_PERSONA_KG,
)

estado_autobus: dict = {
    "pasajeros_a_bordo": 0,
    "parada_actual": "Chinandega",
    "total_caja_colectada": 0.0,
    "total_carga_colectada": 0.0,
    "carga_abordo": 0,
    "peso_estimado_kg": 0.0,
    "ruta_activa": None,
    "bus_id": "BUS-001",
}

historial_viajes: list[dict] = []

# ── simulation state ─────────────────────────────────────────────────────────

_pasajeros_simulados: list[dict] = []
_contador_sim: int = 0
_auto_thread: threading.Thread | None = None
_auto_detener = threading.Event()
NOMBRES_AUTO = ["Ana", "Luis", "Maria", "Juan", "Sofia",
                "Carlos", "Elena", "Pedro", "Laura", "Diego"]
