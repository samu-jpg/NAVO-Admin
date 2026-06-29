"""GPS utilities: distance calculation, stop detection, and fare processing."""
from math import radians, cos, sin, asin, sqrt
from .data_manager import obtener_ruta


def calcular_distancia(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the Haversine distance between two GPS coordinates in meters."""
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    return 6371000 * c


def obtener_parada_actual(
    lat: float, lon: float, ruta_id: str | None = None, radio_tolerancia: int = 150
) -> str:
    """Return the name of the nearest stop within `radio_tolerancia` meters, or 'En ruta'."""
    if not ruta_id:
        return "En ruta"
    ruta = obtener_ruta(ruta_id)
    if not ruta:
        return "En ruta"
    for parada in ruta["paradas"]:
        dist = calcular_distancia(lat, lon, parada["lat"], parada["lon"])
        if dist <= radio_tolerancia:
            return parada["nombre"]
    return "En ruta"


def procesar_tarifa(origen: str, destino: str, ruta_id: str | None = None) -> float:
    """Look up the fare between two stops for a given route. Returns 0 if no fare is defined."""
    if not ruta_id or origen == destino or origen == "En ruta" or destino == "En ruta":
        return 0.0
    ruta = obtener_ruta(ruta_id)
    if not ruta:
        return 0.0
    clave = f"{origen}-{destino}"
    return ruta["tarifas"].get(clave, 0.0)


def listar_paradas(ruta_id: str) -> list[str]:
    """Return a list of stop names for a route."""
    ruta = obtener_ruta(ruta_id)
    if not ruta:
        return []
    return [p["nombre"] for p in ruta["paradas"]]


def probar_gps(lat: float, lon: float, ruta_id: str) -> str:
    """Test GPS by checking which stop (if any) the coordinates fall within."""
    return obtener_parada_actual(lat, lon, ruta_id)
