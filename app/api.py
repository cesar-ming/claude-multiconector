"""API REST (para la plataforma Marketificial y el panel) y callbacks OAuth."""
from __future__ import annotations

import hmac
import html
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .http import ErrorPlataforma, ErrorUsuario
from .servicio import Servicio

PANEL = Path(__file__).parent / "panel.html"


class CredManual(BaseModel):
    login: str
    datos: dict[str, Any]
    etiqueta: str | None = None


class Vincular(BaseModel):
    credencial_id: str
    cliente: str
    plataforma: str
    cuenta_id: str


class Propuesta(BaseModel):
    conexion_id: str
    accion: str
    parametros: dict[str, Any] = Field(default_factory=dict)


def crear_api(servicio_dep) -> FastAPI:
    app = FastAPI(title="Conector Marketificial", version="1.0.0",
                  description="Meta Ads, Google Ads, LinkedIn Ads, TikTok Ads, Facebook, Instagram, TikTok, "
                              "LinkedIn, Shopify y WordPress en una sola API.")

    def svc() -> Servicio:
        return servicio_dep()

    def clave(x_api_key: str = Header(default="")) -> None:
        esperada = svc().a.api_key
        if not esperada or not hmac.compare_digest(x_api_key, esperada):
            raise HTTPException(401, "Clave de API incorrecta")

    @app.exception_handler(ErrorUsuario)
    async def _eu(_: Request, e: ErrorUsuario):
        return JSONResponse({"error": str(e)}, status_code=400)

    @app.exception_handler(ErrorPlataforma)
    async def _ep(_: Request, e: ErrorPlataforma):
        return JSONResponse(e.a_dict(), status_code=502)

    @app.get("/salud")
    async def salud():
        return {"ok": True}

    @app.get("/", include_in_schema=False)
    async def raiz():
        return RedirectResponse("/panel")

    @app.get("/panel", response_class=HTMLResponse, include_in_schema=False)
    async def panel():
        return HTMLResponse(PANEL.read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                                     "Referrer-Policy": "no-referrer"})

    @app.get("/oauth/callback/{login_id}", response_class=HTMLResponse, include_in_schema=False)
    async def callback(login_id: str, request: Request):
        try:
            cid = await svc().completar_oauth(login_id, dict(request.query_params))
        except (ErrorUsuario, ErrorPlataforma) as e:
            return HTMLResponse(f"<h2>No se pudo conectar</h2><p>{html.escape(str(e))}</p>"
                                "<p><a href='/panel'>Volver al panel</a></p>", status_code=400)
        return RedirectResponse(f"/panel?credencial={cid}", status_code=303)

    r = APIRouter(prefix="/api", dependencies=[Depends(clave)])

    @r.get("/catalogo")
    async def catalogo():
        return svc().catalogo()

    @r.post("/oauth/{login_id}/enlace")
    async def enlace(login_id: str, tienda: str | None = None, etiqueta: str | None = None):
        return {"url": svc().enlace_oauth(login_id, tienda, etiqueta)}

    @r.get("/credenciales")
    async def credenciales():
        return svc().db.listar_credenciales()

    @r.post("/credenciales/manual")
    async def manual(c: CredManual):
        return {"credencial_id": await svc().credencial_manual(c.login, c.datos, c.etiqueta, "api")}

    @r.delete("/credenciales/{cid}")
    async def borrar_cred(cid: str):
        svc().db.borrar_credencial(cid)
        svc().db.auditar("borrar_credencial", True, "api", detalle={"credencial_id": cid})
        return {"ok": True}

    @r.get("/credenciales/{cid}/cuentas")
    async def cuentas(cid: str):
        return await svc().cuentas_disponibles(cid)

    @r.post("/conexiones")
    async def vincular(v: Vincular):
        return await svc().vincular(v.credencial_id, v.cliente, v.plataforma, v.cuenta_id, "api")

    @r.get("/conexiones")
    async def conexiones(cliente: str | None = None):
        return svc().db.listar_conexiones(cliente)

    @r.delete("/conexiones/{cid}")
    async def desvincular(cid: str):
        svc().db.desvincular(cid)
        svc().db.auditar("desvincular", True, "api", cid)
        return {"ok": True}

    @r.get("/conexiones/{cid}/metricas")
    async def metricas(cid: str, desde: str, hasta: str, nivel: str | None = None,
                       metricas: str | None = Query(default=None, description="lista separada por comas")):
        opciones = {k: v for k, v in {"nivel": nivel, "metricas": metricas}.items() if v}
        return await svc().metricas(cid, desde, hasta, opciones)

    @r.get("/conexiones/{cid}/contenido")
    async def contenido(cid: str, limite: int = 25):
        return await svc().contenido(cid, {"limite": limite})

    @r.get("/clientes/{cliente}/resumen")
    async def resumen(cliente: str, desde: str, hasta: str):
        return await svc().resumen_cliente(cliente, desde, hasta)

    @r.post("/acciones")
    async def proponer(p: Propuesta):
        return await svc().proponer(p.conexion_id, p.accion, p.parametros, "api")

    @r.post("/acciones/{token}/confirmar")
    async def confirmar(token: str):
        return await svc().confirmar(token, "api")

    @r.post("/acciones/{token}/cancelar")
    async def cancelar(token: str):
        return svc().cancelar(token, "api")

    @r.get("/acciones")
    async def acciones(estado: str | None = None, limite: int = 50):
        return svc().db.listar_acciones(estado, limite)

    @r.get("/auditoria")
    async def auditoria(limite: int = 100):
        return svc().db.listar_auditoria(limite)

    app.include_router(r)
    return app
