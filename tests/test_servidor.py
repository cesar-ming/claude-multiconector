"""Pruebas de la app completa: API REST, panel y servidor MCP por HTTP."""
import httpx
import pytest
from starlette.testclient import TestClient

from app import main
from app.config import get_settings
from app.db import Almacen
from app.servicio import Servicio
from tests.conftest import Simulador, ajustes


@pytest.fixture
def cliente(monkeypatch):
    a = ajustes()
    monkeypatch.setattr(main, "get_settings", lambda: a)
    sim = Simulador()
    app = main.crear_app(lambda: Servicio(a, Almacen(":memory:", a.clave_cifrado), transport=httpx.MockTransport(sim)))
    with TestClient(app, base_url="http://localhost") as c:
        yield c


def test_api_exige_clave(cliente):
    assert cliente.get("/salud").json() == {"ok": True}
    assert cliente.get("/api/catalogo").status_code == 401
    r = cliente.get("/api/catalogo", headers={"X-API-Key": "clave"})
    assert r.status_code == 200 and len(r.json()["plataformas"]) == 10


def test_panel_se_sirve(cliente):
    r = cliente.get("/panel")
    assert r.status_code == 200 and "Conector Marketificial" in r.text


def test_enlace_oauth_y_callback_invalido(cliente):
    r = cliente.post("/api/oauth/meta/enlace", headers={"X-API-Key": "clave"})
    assert r.json()["url"].startswith("https://www.facebook.com/v27.0/dialog/oauth?")
    r = cliente.get("/oauth/callback/meta?state=falso&code=x")
    assert r.status_code == 400 and "caducado" in r.text


def test_error_usuario_es_400(cliente):
    r = cliente.get("/api/conexiones/noexiste/metricas?desde=2026-11-01&hasta=2026-11-02", headers={"X-API-Key": "clave"})
    assert r.status_code == 400 and "No existe" in r.json()["error"]


MCP_CAB = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
           "MCP-Protocol-Version": "2025-06-18"}


def _rpc(cliente, metodo, params=None, token="tok", id_=1):
    cab = dict(MCP_CAB)
    if token:
        cab["Authorization"] = f"Bearer {token}"
    return cliente.post("/mcp", headers=cab, json={"jsonrpc": "2.0", "id": id_, "method": metodo, "params": params or {}})


def test_mcp_protegido(cliente):
    assert _rpc(cliente, "tools/list", token=None).status_code == 401
    assert _rpc(cliente, "tools/list", token="malo").status_code == 401


def test_mcp_lista_herramientas_y_llama(cliente):
    init = _rpc(cliente, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "test", "version": "1"}})
    assert init.status_code == 200, init.text
    r = _rpc(cliente, "tools/list", id_=2)
    assert r.status_code == 200, r.text
    nombres = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"conexiones", "metricas", "proponer_accion", "confirmar_accion", "resumen_cliente"} <= nombres
    confirmar = next(t for t in r.json()["result"]["tools"] if t["name"] == "confirmar_accion")
    assert confirmar["annotations"]["destructiveHint"] is True
    r = _rpc(cliente, "tools/call", {"name": "conexiones", "arguments": {}}, id_=3)
    assert r.status_code == 200 and r.json()["result"]["isError"] is False
    r = _rpc(cliente, "tools/call", {"name": "confirmar_accion", "arguments": {"token": "inventado"}}, id_=4)
    assert "Token desconocido" in r.text
    # token por parámetro de URL (para conectores que no permiten cabeceras)
    r = cliente.post("/mcp?token=tok", headers=MCP_CAB, json={"jsonrpc": "2.0", "id": 5, "method": "tools/list"})
    assert r.status_code == 200


def test_mcp_en_navegador_muestra_pagina(cliente):
    r = cliente.get("/mcp?token=cualquiera", headers={"Accept": "text/html,application/xhtml+xml"})
    assert r.status_code == 200 and "conector personalizado" in r.text
    assert "cualquiera" not in r.text  # nunca repite el token recibido
