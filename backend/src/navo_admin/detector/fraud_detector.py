"""
Fraud detection rules for bus passenger auditing.

Monitors boarding / alighting events and raises alerts for:
- Passengers who board and alight at the same stop
- Persons alighting without having boarded
- Persons remaining aboard at route end (suspected crew)
- Overcapacity and overweight conditions
"""
from datetime import datetime, timezone, timedelta

ESTADO_BUS = "DETENIDO"
PASAJEROS_ABORDO: dict[int, dict] = {}
CARGA_ABORDO: list[dict] = []
ALERTAS: list[dict] = []
INCIDENTES: list[dict] = []

VENTANA_AGRUPACION_S = 300

LIMITE_PASAJEROS = 80
PESO_PROMEDIO_PERSONA_KG = 70
LIMITE_PESO_TOTAL_KG = 6800

RECOMENDACIONES = {
    "cobrador_busero": "Verify that all collectors and drivers are registered and carry visible ID. Unauthorised personnel bypass revenue controls and reduce vehicle lifespan.",
    "tripulacion_sospechosa": "Review passengers who never alighted. They may be unauthorised crew or persons without fare intent, affecting route profitability.",
    "sobreaforo": "Do not exceed 80 passengers. Overloading shortens tyre, brake, and suspension life and compromises safety.",
    "sobrepeso": "Reduce on-board cargo. Excess weight damages transmission and chassis, reducing bus lifespan and increasing fuel consumption.",
    "persona_no_pasajera": "Verify that all persons aboard are legitimate passengers. Repeated on/off at the same stop may indicate unauthorised activity.",
}

MOTIVOS_BASE = {
    "cobrador_busero": "Person alighted without having boarded",
    "tripulacion_sospechosa": "Never alighted during the entire route",
    "sobreaforo": "Excess passengers aboard",
    "sobrepeso": "Estimated weight exceeds safe limit",
    "persona_no_pasajera": "Person boarded and alighted at the same stop",
}


def _agregar_alerta(tipo: str, timestamp: str, detalles_extra: dict | None = None, agrupar: bool = True) -> None:
    """Add or aggregate an alert. If *agrupar* is True and the same alert type
    fired within the aggregation window, the counter is incremented instead."""
    global ALERTAS

    ultimo = None
    if agrupar:
        for a in reversed(ALERTAS):
            if a["tipo"] == tipo:
                t_ultimo = datetime.fromisoformat(a["ultimo_timestamp"])
                t_ahora = datetime.fromisoformat(timestamp)
                if (t_ahora - t_ultimo).total_seconds() <= VENTANA_AGRUPACION_S:
                    ultimo = a
                break

    entrada_detalle = {"fecha": timestamp}
    entrada_detalle["motivo"] = MOTIVOS_BASE.get(tipo, "")
    if detalles_extra:
        entrada_detalle.update(detalles_extra)
    entrada_detalle["recomendacion"] = RECOMENDACIONES.get(tipo, "")

    if ultimo:
        ultimo["conteo"] += 1
        ultimo["ultimo_timestamp"] = timestamp
        c = ultimo["conteo"]
        ultimo["mensaje"] = f"{MOTIVOS_BASE.get(tipo, tipo)} x{c}"
        ultimo["detalle"].append(entrada_detalle)
    else:
        mensaje = MOTIVOS_BASE.get(tipo, tipo)
        if tipo in ("cobrador_busero", "sobreaforo", "sobrepeso"):
            mensaje += " x1"
        ALERTAS.append({
            "tipo": tipo,
            "conteo": 1,
            "ultimo_timestamp": timestamp,
            "mensaje": mensaje,
            "recomendacion": RECOMENDACIONES.get(tipo, ""),
            "detalle": [entrada_detalle],
        })


# ── public API ──────────────────────────────────────────────────────────────


def set_estado_bus(nuevo_estado: str) -> None:
    """Update the bus state (``DETENIDO`` / ``EN_RUTA``)."""
    global ESTADO_BUS
    ESTADO_BUS = nuevo_estado


def get_estado_bus() -> str:
    """Return the current bus state."""
    return ESTADO_BUS


def verificar_limites(pasajeros_abordo: int, peso_total_kg: float | None = None) -> None:
    """Raise overcapacity / overweight alerts if limits are exceeded."""
    now = datetime.now(timezone.utc).isoformat()
    if pasajeros_abordo > LIMITE_PASAJEROS:
        exceso = pasajeros_abordo - LIMITE_PASAJEROS
        _agregar_alerta(
            "sobreaforo", now,
            {"pasajeros": pasajeros_abordo, "exceso": exceso,
             "motivo": f"{pasajeros_abordo} passengers aboard, limit is {LIMITE_PASAJEROS}"},
        )
    if peso_total_kg and peso_total_kg > LIMITE_PESO_TOTAL_KG:
        exceso = int(peso_total_kg - LIMITE_PESO_TOTAL_KG)
        _agregar_alerta(
            "sobrepeso", now,
            {"peso_kg": peso_total_kg, "exceso_kg": exceso,
             "motivo": f"Estimated weight {peso_total_kg} kg, limit {LIMITE_PESO_TOTAL_KG} kg"},
        )


def pasajero_subio(track_id: int, parada: str, **kwargs) -> bool:
    """Register a passenger boarding.

    Returns False if the person was already aboard (ignores duplicate).
    Triggers a ``persona_no_pasajera`` alert if the same ID boards and
    alights at the same stop repeatedly.
    """
    global PASAJEROS_ABORDO
    now = datetime.now(timezone.utc)

    if track_id in PASAJEROS_ABORDO:
        prev = PASAJEROS_ABORDO[track_id]
        if ESTADO_BUS == "DETENIDO" and prev["parada"] == parada:
            ts = now.isoformat()
            INCIDENTES.append({
                "tipo": "persona_no_pasajera",
                "track_id": track_id,
                "parada": parada,
                "timestamp": ts,
                "mensaje": f"Person {track_id} boarded and alighted at {parada} — likely not a passenger",
            })
            _agregar_alerta("persona_no_pasajera", ts,
                            {"track_id": track_id, "parada": parada})
            del PASAJEROS_ABORDO[track_id]
            return False
        return False

    PASAJEROS_ABORDO[track_id] = {
        "parada": parada,
        "subio_en": now.isoformat(),
        "nombre": kwargs.get("nombre", f"ID {track_id}"),
    }
    return True


def pasajero_bajo(track_id: int, parada: str) -> bool:
    """Register a passenger alighting.

    Returns False if the person was not aboard (triggers a
    ``cobrador_busero`` alert). Also flags same-stop boarding/alighting.
    """
    global PASAJEROS_ABORDO
    now = datetime.now(timezone.utc).isoformat()

    if track_id not in PASAJEROS_ABORDO:
        _agregar_alerta(
            "cobrador_busero", now,
            {"track_id": track_id, "parada": parada,
             "motivo": f"Track {track_id} alighted at {parada} without having boarded"},
        )
        return False

    origen = PASAJEROS_ABORDO[track_id]["parada"]
    nombre = PASAJEROS_ABORDO[track_id].get("nombre", f"ID {track_id}")

    if ESTADO_BUS == "DETENIDO" and origen == parada:
        ts = datetime.now(timezone.utc).isoformat()
        INCIDENTES.append({
            "tipo": "persona_no_pasajera",
            "track_id": track_id,
            "nombre": nombre,
            "parada": parada,
            "timestamp": ts,
            "mensaje": f"{nombre} boarded and alighted at {parada} — likely not a passenger",
        })
        _agregar_alerta("persona_no_pasajera", ts,
                        {"track_id": track_id, "nombre": nombre, "parada": parada})
        del PASAJEROS_ABORDO[track_id]
        return False

    del PASAJEROS_ABORDO[track_id]
    return True


def agregar_carga(descripcion: str = "Bulto", tarifa: float = 10.0) -> float:
    """Register a cargo item boarding the bus."""
    global CARGA_ABORDO
    now = datetime.now(timezone.utc).isoformat()
    CARGA_ABORDO.append({
        "id": len(CARGA_ABORDO) + 1,
        "descripcion": descripcion,
        "tarifa": tarifa,
        "timestamp": now,
    })
    return tarifa


def finalizar_ruta() -> None:
    """End the route: flag all remaining passengers as suspected crew."""
    global ALERTAS
    now = datetime.now(timezone.utc).isoformat()

    for tid, info in list(PASAJEROS_ABORDO.items()):
        nombre = info.get("nombre", f"ID {tid}")
        _agregar_alerta(
            "tripulacion_sospechosa", now,
            {"track_id": tid, "nombre": nombre,
             "parada_subida": info["parada"],
             "subio_en": info["subio_en"],
             "motivo": f"{nombre} boarded at {info['parada']} and never alighted"},
            agrupar=False,
        )

    PASAJEROS_ABORDO.clear()


def get_alertas() -> list[dict]:
    """Return all accumulated fraud alerts."""
    return list(ALERTAS)


def get_incidentes() -> list[dict]:
    """Return all incidents."""
    return list(INCIDENTES)


def limpiar_alertas() -> None:
    """Clear all alerts (cargo data is preserved)."""
    global ALERTAS, CARGA_ABORDO
    ALERTAS = []


def limpiar_todo() -> None:
    """Reset every in-memory structure (alerts, incidents, passengers, cargo)."""
    global ALERTAS, INCIDENTES, PASAJEROS_ABORDO, CARGA_ABORDO
    ALERTAS = []
    INCIDENTES = []
    PASAJEROS_ABORDO = {}
    CARGA_ABORDO = []
