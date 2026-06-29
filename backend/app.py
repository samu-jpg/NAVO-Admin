"""
Backward-compatible entry point for running the server.

Usage::

    uvicorn app:app --reload

This re-exports the FastAPI application from the navo_admin package.
"""
import sys
from pathlib import Path

# Ensure the src directory is on the path so ``import navo_admin`` works.
_src = Path(__file__).resolve().parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from navo_admin.api import app
