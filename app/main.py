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

PAGINA_MCP = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Conector Marketificial</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;font:16px/1.6 system-ui,sans-serif;
background:#f7f7f9;color:#14161a;padding:16px}main{max-width:560px;background:#fff;border:1px solid #e3e5ea;
border-radius:10px;padding:28px}h1{font-size:22px;margin:0 0 8px}code{background:#f1f2f5;padding:1px 5px;border-radius:4px}</style>
</head><body><main><h1>Conector Marketificial para Claude</h1>
<p>Esta dirección no se abre en el navegador: es la entrada que usa Claude para hablar con el conector.</p>
<p>Para usarla, añádela en Claude como <b>conector personalizado</b>, copiando la URL completa (con <code>?token=…</code>).</p>
<p>Para conectar cuentas de clientes, entra en el <a href="/panel">panel</a>.</p></main></body></html>""".encode()


def servicio() -> Servicio:
    return _estado["svc"]


class AuthMCP:
    """Protege /mcp con MCP_TOKEN (cabecera 'Authorization: Bearer …' o parámetro ?token=…)."""

    def __init__(self, app, token_fn):
        self.app, self.token_fn = app, token_fn

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/mcp") and scope["method"] == "GET":
            cab = dict(scope.get("headers") or [])
            if b"text/html" in cab.get(b"accept", b""):
                await send({"type": "http.response.start", "status": 200,
                            "headers": [(b"content-type", b"text/html; charset=utf-8"),
                                        (b"cache-control", b"no-store"), (b"referrer-policy", b"no-referrer")]})
                await send({"type": "http.response.body", "body": PAGINA_MCP})
                return
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
