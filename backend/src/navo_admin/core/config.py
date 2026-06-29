"""Application paths and configuration constants."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent  # backend/
BACKEND_DIR = BASE_DIR
FRONTEND_DIR = BASE_DIR.parent / "frontend"
DATA_DIR = BACKEND_DIR / "data"
RUTAS_DIR = DATA_DIR / "rutas"
ASIGNACIONES_FILE = DATA_DIR / "asignaciones.json"
