"""
FastAPI application for navo-admin.

Mounts all route handlers under ``/api/`` and serves the frontend static files.
"""
import os
import sys
import signal
import subprocess
import threading
import time
import random
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import requests

from ..core import (
    listar_rutas, obtener_ruta, crear_ruta,
    actualizar_ruta, eliminar_ruta,
    listar_asignaciones, asignar_bus,
    desasignar_bus, obtener_asignacion,
)
from ..core.gps import obtener_parada_actual, procesar_tarifa, listar_paradas, probar_gps
from ..core.config import BACKEND_DIR
from ..detector.fraud_detector import (
    pasajero_subio, pasajero_bajo, set_estado_bus,
    get_estado_bus, get_alertas, get_incidentes,
    limpiar_alertas, finalizar_ruta, agregar_carga,
    verificar_limites,
    PASAJEROS_ABORDO, CARGA_ABORDO,
    LIMITE_PASAJEROS, PESO_PROMEDIO_PERSONA_KG, LIMITE_PESO_TOTAL_KG,
)
from ..models import (
    EventoSubida, EventoBajada, CambioEstado,
    RutaCreate, RutaUpdate, AsignacionCreate,
    SimularPasajero, CamUrl,
)
from . import state


# ── FastAPI instance ─────────────────────────────────────────────────────────

app = FastAPI(title="navo-admin — Bus Audit System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── helpers ──────────────────────────────────────────────────────────────────

API_BASE = "http://localhost:8000"
proceso_yolo = None


# ── status endpoint ──────────────────────────────────────────────────────────

@app.get("/api/estado")
def obtener_estado_unidad():
    """Return the current bus status including passenger count, location,
    active route, and fraud-detection state."""
    asignacion = obtener_asignacion(state.estado_autobus["bus_id"])
    ruta_id = asignacion["ruta_id"] if asignacion else None
    ruta = obtener_ruta(ruta_id) if ruta_id else None
    paradas = listar_paradas(ruta_id) if ruta_id else []
    return {
        **state.estado_autobus,
        "ruta_activa": ruta["nombre"] if ruta else None,
        "ruta_id": ruta_id,
        "paradas": paradas,
        "estado_bus": get_estado_bus(),
    }


# ── passenger events ─────────────────────────────────────────────────────────

@app.get("/api/historial")
def obtener_historial_viajes():
    """Return the full travel history for the current session."""
    return state.historial_viajes


@app.post("/api/pasajero-subio")
def api_pasajero_subio(body: EventoSubida):
    """Record a passenger boarding event (triggered by detector or simulation)."""
    parada = state.estado_autobus["parada_actual"]
    valido = pasajero_subio(body.track_id, parada)
    if valido:
        state.estado_autobus["pasajeros_a_bordo"] += 1
        return {"status": "ok", "mensaje": f"Passenger {body.track_id} boarded at {parada}"}
    return {"status": "ok", "mensaje": f"Person {body.track_id} was already aboard"}


@app.post("/api/pasajero-bajo")
def api_pasajero_bajo(body: EventoBajada):
    """Record a passenger alighting event. Fares are calculated and logged.
    Returns an alert if the person boards and alights at the same stop."""
    parada = state.estado_autobus["parada_actual"]
    track_id = body.track_id
    info = PASAJEROS_ABORDO.get(track_id)

    if not info:
        pasajero_bajo(track_id, parada)
        return {"status": "error", "mensaje": f"Person {track_id} was not aboard"}

    origen = info["parada"]
    nombre = info.get("nombre", f"ID {track_id}")

    if get_estado_bus() == "DETENIDO" and origen == parada:
        pasajero_bajo(track_id, parada)
        return {
            "status": "alerta",
            "mensaje": f"Fraud: Person {track_id} boarded and alighted at {parada}",
            "alerta": True,
        }

    pasajero_bajo(track_id, parada)
    state.estado_autobus["pasajeros_a_bordo"] -= 1

    asignacion = obtener_asignacion(state.estado_autobus["bus_id"])
    ruta_id = asignacion["ruta_id"] if asignacion else None
    monto = procesar_tarifa(origen, parada, ruta_id)

    if monto > 0:
        state.estado_autobus["total_caja_colectada"] += monto
        state.historial_viajes.append({
            "id": f"ID_{len(state.historial_viajes) + 1:03d}",
            "track_id": track_id,
            "nombre": nombre,
            "origen": origen,
            "destino": parada,
            "tarifa": monto,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return {"status": "ok", "mensaje": f"{nombre} alighted at {parada}. Fare: C$ {monto:.2f}", "tarifa": monto}

    return {"status": "ok", "mensaje": f"{nombre} alighted (no fare)"}


# ── bus state control ────────────────────────────────────────────────────────

@app.post("/api/cambiar-estado-bus")
def api_cambiar_estado(body: CambioEstado):
    """Change the bus state (DETENIDO / EN_RUTA) and optionally the current stop."""
    if body.parada:
        state.estado_autobus["parada_actual"] = body.parada
    set_estado_bus(body.estado)
    return {"status": "ok", "estado": body.estado, "parada": state.estado_autobus["parada_actual"]}


@app.post("/api/finalizar-ruta")
def api_finalizar_ruta():
    """End the current route: clears passengers and flags remaining aboard as suspicious."""
    finalizar_ruta()
    state.estado_autobus["pasajeros_a_bordo"] = 0
    return {"status": "ok", "mensaje": "Route finished"}


# ── alerts ───────────────────────────────────────────────────────────────────

@app.get("/api/alertas-fraude")
def api_alertas_fraude():
    """Return all fraud alerts."""
    return get_alertas()


@app.get("/api/incidentes")
def api_incidentes():
    """Return all incidents."""
    return get_incidentes()


@app.delete("/api/alertas-fraude")
def api_limpiar_alertas():
    """Clear all fraud alerts."""
    limpiar_alertas()
    return {"status": "ok"}


# ── route CRUD ───────────────────────────────────────────────────────────────

@app.get("/api/rutas")
def api_listar_rutas():
    """List all routes."""
    return listar_rutas()


@app.get("/api/rutas/{ruta_id}")
def api_obtener_ruta(ruta_id: str):
    """Get a single route by id."""
    ruta = obtener_ruta(ruta_id)
    if not ruta:
        return {"error": "Route not found"}, 404
    return ruta


@app.post("/api/rutas")
def api_crear_ruta(body: RutaCreate):
    """Create a new route."""
    data = body.model_dump()
    paradas = data.get("paradas", [])
    if paradas and isinstance(paradas[0], str):
        data["paradas"] = [{"nombre": p, "lat": 12.5, "lon": -86.9} for p in paradas]
    ruta = crear_ruta(data)
    return {"status": "ok", "ruta": ruta}


@app.put("/api/rutas/{ruta_id}")
def api_actualizar_ruta(ruta_id: str, body: RutaUpdate):
    """Update an existing route."""
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    ruta = actualizar_ruta(ruta_id, data)
    if not ruta:
        return {"error": "Route not found"}, 404
    return {"status": "ok", "ruta": ruta}


@app.delete("/api/rutas/{ruta_id}")
def api_eliminar_ruta(ruta_id: str):
    """Delete a route."""
    eliminar_ruta(ruta_id)
    return {"status": "ok"}


@app.post("/api/rutas/{ruta_id}/probar-gps")
def api_probar_gps(ruta_id: str, lat: float, lon: float):
    """Test GPS coordinates against a route's stops."""
    parada = probar_gps(lat, lon, ruta_id)
    return {"lat": lat, "lon": lon, "parada": parada}


# ── assignments ──────────────────────────────────────────────────────────────

@app.get("/api/asignaciones")
def api_listar_asignaciones():
    """List all bus-to-route assignments with enriched info."""
    asignaciones = listar_asignaciones()
    resultado = []
    for a in asignaciones:
        ruta = obtener_ruta(a["ruta_id"])
        activa = a.get("bus_id") == state.estado_autobus.get("bus_id")
        resultado.append({
            **a,
            "conductor": a.get("nombre", a.get("bus_id")),
            "ruta_nombre": ruta["nombre"] if ruta else None,
            "activa": activa,
            "parada_actual": state.estado_autobus["parada_actual"] if activa else None,
        })
    return {"asignaciones": resultado}


@app.post("/api/asignaciones")
def api_asignar_bus(body: AsignacionCreate):
    """Assign a bus to a route."""
    data = body.model_dump(exclude_none=True)
    if "conductor" in data and "nombre" not in data:
        data["nombre"] = data.pop("conductor")
    resultado = asignar_bus(data)
    state.estado_autobus["bus_id"] = data["bus_id"]
    ruta = obtener_ruta(data["ruta_id"])
    if ruta and ruta["paradas"]:
        state.estado_autobus["parada_actual"] = ruta["paradas"][0]["nombre"]
    return {"status": "ok", "asignacion": resultado}


@app.delete("/api/asignaciones/{bus_id}")
def api_desasignar_bus(bus_id: str):
    """Remove a bus assignment."""
    desasignar_bus(bus_id)
    return {"status": "ok"}


# ── simulation ───────────────────────────────────────────────────────────────

@app.post("/api/simular")
def api_simular(body: SimularPasajero):
    """Manually simulate a passenger boarding / alighting or cargo event."""
    if body.accion == "sube":
        state._contador_sim += 1
        tid = 9000 + state._contador_sim
        parada = state.estado_autobus["parada_actual"]
        nombre = body.nombre or f"Passenger {state._contador_sim}"
        pasajero_subio(tid, parada, nombre=nombre)
        state.estado_autobus["pasajeros_a_bordo"] += 1
        state._pasajeros_simulados.append({"track_id": tid, "nombre": nombre})
        peso_actual = state.estado_autobus["pasajeros_a_bordo"] * PESO_PROMEDIO_PERSONA_KG
        verificar_limites(state.estado_autobus["pasajeros_a_bordo"], peso_actual)
        return {"status": "ok", "track_id": tid, "mensaje": f"{nombre} boarded at {parada}"}

    elif body.accion == "baja":
        parada = state.estado_autobus["parada_actual"]
        asignacion = obtener_asignacion(state.estado_autobus["bus_id"])
        ruta_id = asignacion["ruta_id"] if asignacion else None
        if state._pasajeros_simulados:
            p = state._pasajeros_simulados.pop(0)
            tid = p["track_id"]
            origen = PASAJEROS_ABORDO.get(tid, {}).get("parada", parada)
            pasajero_bajo(tid, parada)
            state.estado_autobus["pasajeros_a_bordo"] -= 1
            monto = procesar_tarifa(origen, parada, ruta_id)
            if monto > 0:
                state.estado_autobus["total_caja_colectada"] += monto
                state.historial_viajes.append({
                    "id": f"ID_{len(state.historial_viajes) + 1:03d}",
                    "track_id": tid, "nombre": p["nombre"],
                    "origen": origen, "destino": parada,
                    "tarifa": monto,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            return {"status": "ok", "mensaje": f"{p['nombre']} alighted at {parada}. Fare: C$ {monto:.2f}", "tarifa": monto}
        return {"status": "error", "mensaje": "No passengers aboard"}

    elif body.accion == "carga":
        tarifa = agregar_carga(body.nombre or "Cargo")
        state.estado_autobus["total_carga_colectada"] += tarifa
        state.estado_autobus["carga_abordo"] += 1
        return {"status": "ok", "mensaje": f"Cargo registered: C$ {tarifa:.0f}", "tarifa": tarifa}

    return {"status": "error", "mensaje": "Invalid action"}


# ── auto simulation mode ────────────────────────────────────────────────────

def _loop_auto():
    """Background thread that simulates passengers boarding and alighting
    automatically for demo purposes."""
    idx = 0
    state._pasajeros_simulados = []
    state.estado_autobus["pasajeros_a_bordo"] = 0
    state.estado_autobus["total_caja_colectada"] = 0.0
    state.estado_autobus["total_carga_colectada"] = 0.0
    state.estado_autobus["carga_abordo"] = 0
    state.estado_autobus["peso_estimado_kg"] = 0.0

    paradas_cache = None
    ruta_id_cache = None
    parada_idx = 0
    subidas_en_parada = 0

    while not state._auto_detener.is_set():
        if paradas_cache is None:
            asignacion = obtener_asignacion(state.estado_autobus["bus_id"])
            ruta_id_cache = asignacion["ruta_id"] if asignacion else None
            ruta = obtener_ruta(ruta_id_cache) if ruta_id_cache else None
            paradas_cache = (
                [p["nombre"] for p in (ruta["paradas"] if ruta else [])]
                or ["Chinandega", "Posoltega", "Leon"]
            )
            state.estado_autobus["parada_actual"] = paradas_cache[0]
            set_estado_bus("DETENIDO")

        if get_estado_bus() == "EN_RUTA":
            subidas_en_parada = 0
            if len(paradas_cache) > 2 and random.random() < 0.3:
                parada_idx = (parada_idx + 2) % len(paradas_cache)
            else:
                parada_idx = (parada_idx + 1) % len(paradas_cache)
            parada_actual = paradas_cache[parada_idx]

            for _ in range(random.randint(0, 2)):
                if state._pasajeros_simulados:
                    p = state._pasajeros_simulados.pop(0)
                    tid = p["track_id"]
                    origen = PASAJEROS_ABORDO.get(tid, {}).get("parada", parada_actual)
                    pasajero_bajo(tid, parada_actual)
                    state.estado_autobus["pasajeros_a_bordo"] = max(
                        0, state.estado_autobus["pasajeros_a_bordo"] - 1
                    )
                    monto = procesar_tarifa(origen, parada_actual, ruta_id_cache)
                    if monto > 0:
                        state.estado_autobus["total_caja_colectada"] += monto
                        state.historial_viajes.append({
                            "id": f"ID_{len(state.historial_viajes) + 1:03d}",
                            "track_id": tid, "nombre": p["nombre"],
                            "origen": origen, "destino": parada_actual,
                            "tarifa": monto,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })

            if state.estado_autobus["carga_abordo"] > 0 and random.random() < 0.3:
                state.estado_autobus["carga_abordo"] = max(
                    0, state.estado_autobus["carga_abordo"] - random.randint(0, 2)
                )

            set_estado_bus("DETENIDO")
            state.estado_autobus["parada_actual"] = parada_actual
            state._auto_detener.wait(1.0)

        else:
            subidas_en_parada += 1
            for _ in range(random.randint(1, 3)):
                state._contador_sim += 1
                tid = 9000 + state._contador_sim
                nombre = state.NOMBRES_AUTO[state._contador_sim % len(state.NOMBRES_AUTO)]
                pasajero_subio(tid, state.estado_autobus["parada_actual"], nombre=nombre)
                state._pasajeros_simulados.append({"track_id": tid, "nombre": nombre})
                state.estado_autobus["pasajeros_a_bordo"] += 1

            if random.random() < 0.3:
                for _ in range(random.randint(1, 2)):
                    tarifa = agregar_carga()
                    state.estado_autobus["total_carga_colectada"] += tarifa
                    state.estado_autobus["carga_abordo"] += 1

            peso_actual = state.estado_autobus["pasajeros_a_bordo"] * PESO_PROMEDIO_PERSONA_KG
            state.estado_autobus["peso_estimado_kg"] = peso_actual
            verificar_limites(state.estado_autobus["pasajeros_a_bordo"], peso_actual)

            if subidas_en_parada >= random.randint(2, 4):
                set_estado_bus("EN_RUTA")

            state._auto_detener.wait(1.2 + random.random() * 1.5)

    finalizar_ruta()


@app.post("/api/auto/start")
def api_auto_start():
    """Start the automatic passenger simulation."""
    if state._auto_thread and state._auto_thread.is_alive():
        return {"status": "ok", "mensaje": "Auto mode already running"}
    state._auto_detener.clear()
    state._auto_thread = threading.Thread(target=_loop_auto, daemon=True)
    state._auto_thread.start()
    return {"status": "ok", "mensaje": "Auto mode started"}


@app.post("/api/auto/stop")
def api_auto_stop():
    """Stop the automatic passenger simulation."""
    if not state._auto_thread or not state._auto_thread.is_alive():
        return {"status": "ok", "mensaje": "Auto mode was not running"}
    state._auto_detener.set()
    state._auto_thread.join(timeout=5)
    state._auto_thread = None
    return {"status": "ok", "mensaje": "Auto mode stopped"}


@app.get("/api/auto/status")
def api_auto_status():
    """Check if auto-simulation is running."""
    corriendo = state._auto_thread is not None and state._auto_thread.is_alive()
    return {"corriendo": corriendo}


# ── YOLO subsystem (legacy) ─────────────────────────────────────────────────

@app.post("/api/iniciar-yolo")
def api_iniciar_yolo(body: CamUrl):
    """Start the YOLOX detector as a subprocess.

    The subprocess reads from the given video source and posts events
    to the NAVO API.
    """
    global proceso_yolo
    if proceso_yolo and proceso_yolo.poll() is None:
        return {"status": "error", "mensaje": "YOLOX is already running"}

    cmd = [
        sys.executable, "-u",
        str(BACKEND_DIR / "src" / "navo_admin" / "detector" / "engine.py"),
        "--video", body.cam_url,
        "--api", "http://localhost:8000",
    ]
    log_path = BACKEND_DIR / "yolo_output.log"
    with open(log_path, "w") as log:
        proceso_yolo = subprocess.Popen(
            cmd, stdout=log, stderr=subprocess.STDOUT, cwd=str(BACKEND_DIR),
        )
    return {"status": "ok", "pid": proceso_yolo.pid, "mensaje": "YOLOX started"}


@app.post("/api/detener-yolo")
def api_detener_yolo():
    """Stop the YOLOX subprocess."""
    global proceso_yolo
    if not proceso_yolo or proceso_yolo.poll() is not None:
        proceso_yolo = None
        return {"status": "ok", "mensaje": "YOLOX was already stopped"}
    proceso_yolo.send_signal(signal.SIGINT)
    try:
        proceso_yolo.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proceso_yolo.kill()
    proceso_yolo = None
    api_finalizar_ruta()
    return {"status": "ok", "mensaje": "YOLOX stopped"}


@app.get("/api/estado-yolo")
def api_estado_yolo():
    """Check if the YOLOX subprocess is running."""
    global proceso_yolo
    corriendo = proceso_yolo is not None and proceso_yolo.poll() is None
    return {"corriendo": corriendo, "pid": proceso_yolo.pid if corriendo else None}


@app.post("/api/detectar")
async def api_detectar(file: UploadFile = File(...)):
    """Detect people in an uploaded image using YOLOX.

    Returns bounding boxes + boarding/alighting events.
    """
    from ..detector.service import detectar_personas

    image_bytes = await file.read()
    resultado = detectar_personas(image_bytes)
    if resultado.get("error"):
        return resultado

    detecciones = resultado.get("detecciones", [])
    eventos = resultado.get("eventos", [])
    for d in detecciones:
        d["track_id"] = int(d.get("track_id", 0))
    for ev in eventos:
        tid = ev["track_id"]
        if ev["evento"] == "subio":
            try:
                requests.post(f"{API_BASE}/api/pasajero-subio",
                              json={"track_id": tid}, timeout=2)
            except Exception:
                pass
        elif ev["evento"] == "bajo":
            try:
                requests.post(f"{API_BASE}/api/pasajero-bajo",
                              json={"track_id": tid}, timeout=2)
            except Exception:
                pass
    return {"detecciones": detecciones, "eventos": eventos}


@app.get("/api/tracks")
def api_tracks():
    """Return all active tracked object IDs."""
    from ..detector.tracker import TRACKS
    return {
        "tracks": [
            {"track_id": tid, "lost": t["lost"]}
            for tid, t in TRACKS.items()
        ]
    }


@app.post("/api/reset-tracks")
def api_reset_tracks():
    """Clear all tracked IDs."""
    from ..detector.tracker import reset_tracks
    reset_tracks()
    return {"status": "ok"}


@app.get("/api/pasajeros-abordo")
def api_pasajeros_abordo():
    """Return the list of passengers currently aboard."""
    return {
        "cantidad": len(PASAJEROS_ABORDO),
        "pasajeros": [
            {
                "track_id": tid,
                "parada_subida": info["parada"],
                "subio_en": info["subio_en"],
                "nombre": info.get("nombre", f"ID {tid}"),
            }
            for tid, info in PASAJEROS_ABORDO.items()
        ],
    }


# ── reports ──────────────────────────────────────────────────────────────────

@app.get("/api/informes/pasajeros")
def api_informes_pasajeros(desde: str = None, hasta: str = None):
    """Return passenger trip reports, optionally filtered by date range."""
    viajes = list(state.historial_viajes)
    if desde:
        viajes = [v for v in viajes if v.get("timestamp", "") >= desde]
    if hasta:
        viajes = [v for v in viajes if v.get("timestamp", "") <= hasta + "T23:59:59"]
    viajes.reverse()
    return {
        "viajes": viajes,
        "total_viajes": len(viajes),
        "total_ingresos": sum(v.get("tarifa", 0) for v in viajes),
        "total_pasajeros": len(set(v["track_id"] for v in viajes)) if viajes else 0,
    }


@app.get("/api/informes/vehiculos")
def api_informes_vehiculos():
    """Return vehicle-level aggregated report."""
    viajes = list(state.historial_viajes)
    ingresos = sum(v.get("tarifa", 0) for v in viajes)
    pasajeros_unicos = len(set(v["track_id"] for v in viajes))
    alertas = get_alertas()
    total_carga = sum(c["tarifa"] for c in CARGA_ABORDO)
    return {
        "total_viajes": len(viajes),
        "total_ingresos": ingresos,
        "total_carga": total_carga,
        "total_pasajeros": pasajeros_unicos,
        "alertas": len(alertas),
        "alertas_detalle": alertas[-20:],
        "pasajeros_actuales": len(PASAJEROS_ABORDO),
        "limite_pasajeros": LIMITE_PASAJEROS,
        "limite_peso_kg": LIMITE_PESO_TOTAL_KG,
        "peso_estimado_kg": state.estado_autobus.get("peso_estimado_kg", 0),
    }


# ── frontend static mount ────────────────────────────────────────────────────

frontend_path = Path(__file__).resolve().parent.parent.parent.parent.parent / "frontend"
app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
