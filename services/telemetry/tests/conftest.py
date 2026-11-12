"""Cada servicio tiene su propio paquete `app`; al correr pytest sobre varios
servicios a la vez hay que aislar los módulos para que `import app.main`
cargue el de ESTE servicio."""
import pathlib
import sys

import pytest

ROOT = str(pathlib.Path(__file__).resolve().parents[1])


@pytest.fixture(autouse=True)
def _isolate_app_package():
    for k in list(sys.modules):
        if k == "app" or k.startswith("app."):
            del sys.modules[k]
    sys.path = [p for p in sys.path if not p.endswith("/services/" + pathlib.Path(ROOT).name) or p == ROOT]
    if ROOT in sys.path:
        sys.path.remove(ROOT)
    sys.path.insert(0, ROOT)
    yield
