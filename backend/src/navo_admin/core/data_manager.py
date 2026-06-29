"""
JSON file-backed storage for routes and bus assignments.

Routes are stored as individual files under ``data/rutas/<route_id>.json``
so the dataset scales gracefully as new routes are added.
Assignments live in a single ``data/asignaciones.json`` file.
"""
import json
import shutil
from datetime import datetime, timezone
from .config import RUTAS_DIR, ASIGNACIONES_FILE


# ── helpers ──────────────────────────────────────────────────────────────────


def _leer_json(path) -> list | dict:
    """Read and deserialize a JSON file. Returns an empty list if the file is missing."""
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _escribir_json(path, data) -> None:
    """Atomically write data to a JSON file (write to tmp, then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    shutil.move(str(tmp), str(path))


# ── rutas (one file per route) ──────────────────────────────────────────────


def _ruta_path(route_id: str) -> Path:
    return RUTAS_DIR / f"{route_id}.json"


def listar_rutas() -> list[dict]:
    """Return all routes by reading every JSON file in the rutas directory."""
    if not RUTAS_DIR.exists():
        return []
    rutas = []
    for f in sorted(RUTAS_DIR.iterdir()):
        if f.suffix == ".json":
            rutas.append(_leer_json(f))
    return rutas


def obtener_ruta(ruta_id: str) -> dict | None:
    """Return a single route by id, or None if not found."""
    path = _ruta_path(ruta_id)
    if not path.exists():
        return None
    return _leer_json(path)


def crear_ruta(data: dict) -> dict:
    """Create a new route, generating an id if none is provided."""
    nuevo_id = (data.get("id") or "").strip()
    if not nuevo_id:
        base = data["nombre"].lower().replace(" ", "_").replace("-", "_")
        ts = int(datetime.now(timezone.utc).timestamp())
        nuevo_id = f"ruta_{base}_{ts}"
    data["id"] = nuevo_id
    data.setdefault("paradas", [])
    data.setdefault("tarifas", {})
    _escribir_json(_ruta_path(nuevo_id), data)
    return data


def actualizar_ruta(ruta_id: str, data: dict) -> dict | None:
    """Update an existing route in-place. Returns None if the route does not exist."""
    ruta = obtener_ruta(ruta_id)
    if not ruta:
        return None
    ruta.update(data)
    ruta["id"] = ruta_id
    _escribir_json(_ruta_path(ruta_id), ruta)
    return ruta


def eliminar_ruta(ruta_id: str) -> None:
    """Delete a route file and remove any assignment that references it."""
    path = _ruta_path(ruta_id)
    if path.exists():
        path.unlink()
    asignaciones = listar_asignaciones()
    asignaciones = [a for a in asignaciones if a["ruta_id"] != ruta_id]
    _escribir_json(ASIGNACIONES_FILE, asignaciones)


# ── asignaciones ────────────────────────────────────────────────────────────


def listar_asignaciones() -> list[dict]:
    """Return all bus-to-route assignments."""
    return _leer_json(ASIGNACIONES_FILE)


def asignar_bus(data: dict) -> dict:
    """Assign (or re-assign) a bus to a route. Creates or updates the entry."""
    asignaciones = listar_asignaciones()
    bus_id = data["bus_id"]
    for a in asignaciones:
        if a["bus_id"] == bus_id:
            a["ruta_id"] = data["ruta_id"]
            a["nombre"] = data.get("nombre", a.get("nombre", bus_id))
            _escribir_json(ASIGNACIONES_FILE, asignaciones)
            return a
    data.setdefault("nombre", bus_id)
    asignaciones.append(data)
    _escribir_json(ASIGNACIONES_FILE, asignaciones)
    return data


def desasignar_bus(bus_id: str) -> None:
    """Remove the assignment for a given bus."""
    asignaciones = listar_asignaciones()
    asignaciones = [a for a in asignaciones if a["bus_id"] != bus_id]
    _escribir_json(ASIGNACIONES_FILE, asignaciones)


def obtener_asignacion(bus_id: str) -> dict | None:
    """Return the assignment for a bus, or None."""
    asignaciones = listar_asignaciones()
    for a in asignaciones:
        if a["bus_id"] == bus_id:
            return a
    return None
