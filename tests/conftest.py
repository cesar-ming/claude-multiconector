import json
import re

import httpx
import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.db import Almacen
from app.servicio import Servicio


class Simulador:
    """Transporte HTTP falso: se registran respuestas por método y patrón de URL."""

    def __init__(self):
        self.rutas: list[tuple[str, re.Pattern, object]] = []
        self.llamadas: list[httpx.Request] = []

    def en(self, metodo: str, patron: str, respuesta, estado: int = 200, cabeceras: dict | None = None):
        self.rutas.insert(0, (metodo, re.compile(patron), (respuesta, estado, cabeceras or {})))

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.llamadas.append(req)
        for metodo, patron, (resp, estado, cab) in self.rutas:
            if req.method == metodo and patron.search(str(req.url)):
                cuerpo = resp(req) if callable(resp) else resp
                if isinstance(cuerpo, httpx.Response):
                    return cuerpo
                return httpx.Response(estado, json=cuerpo, headers=cab)
        return httpx.Response(404, json={"error": {"message": f"sin simulación para {req.method} {req.url}"}})

    def ultima(self, patron: str) -> httpx.Request:
        return [r for r in self.llamadas if re.search(patron, str(r.url))][-1]


def ajustes(**kw) -> Settings:
    base = dict(base_url="https://conector.test", api_key="clave", mcp_token="tok",
                clave_cifrado=Fernet.generate_key().decode(), db_path=":memory:",
                meta_app_id="mid", meta_app_secret="msec", google_client_id="gid", google_client_secret="gsec",
                google_developer_token="dev", linkedin_ads_client_id="lid", linkedin_ads_client_secret="lsec",
                linkedin_cm_client_id="cid", linkedin_cm_client_secret="csec", tiktok_ads_app_id="tid",
                tiktok_ads_secret="tsec", tiktok_client_key="tk", tiktok_client_secret="ts",
                shopify_client_id="sid", shopify_client_secret="ssec",
                permitir_publicar=True, permitir_gestionar=True)
    base.update(kw)
    return Settings(_env_file=None, **base)


@pytest.fixture
def sim():
    return Simulador()


@pytest.fixture
def crear_servicio(sim):
    creados = []

    def _crear(**kw):
        a = ajustes(**kw)
        s = Servicio(a, Almacen(":memory:", a.clave_cifrado), transport=httpx.MockTransport(sim))
        creados.append(s)
        return s
    yield _crear


def cuerpo(req: httpx.Request) -> dict:
    return json.loads(req.content or b"{}")
