"""Punto de entrada: API REST + panel + servidor MCP (Streamable HTTP en /mcp) en un solo proceso."""
from __future__ import annotations

import contextlib
import hmac
import json
from urllib.parse import parse_qs

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Mount

from .api import crear_api
from .config import get_settings
from .mcp_servidor import crear_mcp
from .servicio import Servicio

_estado: dict = {}


def servicio() -> Servicio:
    return _estado["svc"]


class AuthMCP:
    """Protege /mcp con MCP_TOKEN (cabecera 'Authorization: Bearer …' o parámetro ?token=…)."""

    def __init__(self, app, token_fn):
        self.app, self.token_fn = app, token_fn

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            esperado = self.token_fn()
            cab = dict(scope.get("headers") or [])
            recibido = cab.get(b"authorization", b"").decode()
            recibido = recibido[7:] if recibido.lower().startswith("bearer ") else ""
            if not recibido:
                recibido = (parse_qs(scope.get("query_string", b"").decode()).get("token") or [""])[0]
            if not esperado or not hmac.compare_digest(recibido, esperado):
                cuerpo = json.dumps({"error": "No autorizado" if esperado else "MCP_TOKEN no configurado"}).encode()
                await send({"type": "http.response.start", "status": 401 if esperado else 503,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": cuerpo})
                return
        await self.app(scope, receive, send)


def crear_app(servicio_factory=None) -> Starlette:
    ajustes = get_settings()
    mcp = crear_mcp(servicio)
    api = crear_api(servicio)
    hosts = ajustes.lista_hosts()
    seguridad = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(hosts),
        allowed_hosts=hosts + ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"],
        allowed_origins=[f"https://{h}" for h in hosts] + ["http://localhost:*", "https://claude.ai"])
    app_mcp = mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True,
                                      transport_security=seguridad, host="0.0.0.0")
    app_mcp.router.routes.append(Mount("/", app=api))

    @contextlib.asynccontextmanager
    async def ciclo(_app):
        _estado["svc"] = servicio_factory() if servicio_factory else Servicio(ajustes)
        async with app_mcp.router.lifespan_context(app_mcp):
            yield
        await _estado["svc"].cerrar()

    externa = Starlette(routes=[Mount("/", app=app_mcp)], lifespan=ciclo)
    externa.add_middleware(AuthMCP, token_fn=lambda: ajustes.mcp_token)
    return externa


app = crear_app()
