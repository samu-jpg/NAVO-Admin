"""Pydantic request/response models for the FastAPI endpoints."""
from pydantic import BaseModel


class EventoSubida(BaseModel):
    track_id: int


class EventoBajada(BaseModel):
    track_id: int


class CambioEstado(BaseModel):
    estado: str
    parada: str | None = None


class RutaCreate(BaseModel):
    id: str | None = None
    nombre: str
    paradas: list = []
    tarifas: dict = {}


class RutaUpdate(BaseModel):
    nombre: str | None = None
    paradas: list | None = None
    tarifas: dict | None = None


class AsignacionCreate(BaseModel):
    bus_id: str
    nombre: str | None = None
    conductor: str | None = None
    ruta_id: str


class SimularPasajero(BaseModel):
    accion: str
    nombre: str | None = None


class CamUrl(BaseModel):
    cam_url: str
