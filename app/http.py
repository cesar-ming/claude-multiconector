"""Cliente HTTP compartido con reintentos y errores homogéneos."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

REINTENTABLES = {429, 500, 502, 503, 504}
_PATRON_SECRETOS = re.compile(
    r"(access_token|refresh_token|client_secret|secret|app_secret|password|token)=([^&\s\"']+)", re.I)


def limpiar(texto: str) -> str:
    """Quita tokens y secretos de URLs o mensajes antes de mostrarlos o registrarlos."""
    return _PATRON_SECRETOS.sub(lambda m: f"{m.group(1)}=***", texto or "")


class ErrorPlataforma(Exception):
    def __init__(self, plataforma: str, mensaje: str, estado: int | None = None, detalle: Any = None):
        mensaje = limpiar(str(mensaje))
        super().__init__(f"[{plataforma}] {mensaje}")
        self.plataforma = plataforma
        self.mensaje = mensaje
        self.estado = estado
        self.detalle = detalle

    def a_dict(self) -> dict:
        detalle = self.detalle
        if detalle is not None:
            try:
                detalle = json.loads(limpiar(json.dumps(detalle)))
            except (TypeError, ValueError):
                detalle = limpiar(str(detalle))
        return {"plataforma": self.plataforma, "error": self.mensaje, "estado_http": self.estado,
                "detalle": detalle}


class ErrorUsuario(Exception):
    """Error por parámetros o estado incorrectos: se devuelve tal cual a quien llama."""


def crear_cliente(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=15.0),
        transport=transport,
        headers={"User-Agent": "Marketificial-Conector/1.0"},
        follow_redirects=False,
    )


async def peticion(cliente: httpx.AsyncClient, plataforma: str, metodo: str, url: str,
                   intentos: int = 3, **kwargs) -> httpx.Response:
    """Hace la petición con reintentos ante 429/5xx y errores de red. Devuelve la respuesta (sin validar)."""
    espera = 1.5
    ultimo: Exception | None = None
    for intento in range(intentos):
        try:
            r = await cliente.request(metodo, url, **kwargs)
        except httpx.TransportError as e:
            ultimo = e
        else:
            if r.status_code not in REINTENTABLES or intento == intentos - 1:
                return r
            retry_after = r.headers.get("retry-after")
            if retry_after and retry_after.isdigit():
                espera = min(float(retry_after), 30.0)
        await asyncio.sleep(espera)
        espera *= 2
    raise ErrorPlataforma(plataforma, f"Sin respuesta tras {intentos} intentos: {limpiar(str(ultimo))}")


def json_o_error(r: httpx.Response, plataforma: str) -> Any:
    """Devuelve el JSON o lanza ErrorPlataforma con el mensaje de la plataforma."""
    try:
        cuerpo = r.json() if r.content else {}
    except ValueError:
        cuerpo = {"texto": r.text[:500]}
    if r.status_code >= 400:
        raise ErrorPlataforma(plataforma, _mensaje_error(cuerpo) or f"HTTP {r.status_code}", r.status_code, cuerpo)
    return cuerpo


def _mensaje_error(cuerpo: Any) -> str:
    if not isinstance(cuerpo, dict):
        return str(cuerpo)[:300]
    e = cuerpo.get("error")
    if isinstance(e, dict):
        return e.get("message") or e.get("error_user_msg") or str(e)[:300]
    if isinstance(e, str):
        return cuerpo.get("error_description") or e
    if "errors" in cuerpo:
        return str(cuerpo["errors"])[:300]
    return cuerpo.get("message") or cuerpo.get("texto") or ""
