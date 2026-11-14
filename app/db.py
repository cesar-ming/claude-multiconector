"""Almacenamiento en SQLite. Las credenciales se guardan cifradas (Fernet)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

ESQUEMA = """
CREATE TABLE IF NOT EXISTS credenciales (
    id TEXT PRIMARY KEY,
    login TEXT NOT NULL,            -- meta, google, linkedin_ads, linkedin_organico, tiktok_ads, tiktok_organico, shopify, wordpress
    etiqueta TEXT,
    datos BLOB NOT NULL,            -- JSON cifrado (tokens)
    caduca TEXT,                    -- ISO UTC del token de acceso, si caduca
    creado TEXT NOT NULL,
    actualizado TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conexiones (
    id TEXT PRIMARY KEY,
    cliente TEXT NOT NULL,
    plataforma TEXT NOT NULL,       -- meta_ads, google_ads, linkedin_ads, tiktok_ads, facebook, instagram, tiktok, linkedin, shopify, wordpress
    cuenta_id TEXT NOT NULL,
    nombre TEXT,
    credencial_id TEXT NOT NULL REFERENCES credenciales(id),
    extra TEXT NOT NULL DEFAULT '{}',
    creado TEXT NOT NULL,
    UNIQUE(cliente, plataforma, cuenta_id)
);
CREATE TABLE IF NOT EXISTS estados_oauth (
    state TEXT PRIMARY KEY,
    login TEXT NOT NULL,
    extra TEXT NOT NULL DEFAULT '{}',
    creado TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS acciones (
    token TEXT PRIMARY KEY,
    conexion_id TEXT NOT NULL,
    accion TEXT NOT NULL,
    parametros TEXT NOT NULL,
    vista_previa TEXT NOT NULL,
    estado TEXT NOT NULL,           -- pendiente, ejecutada, fallida, cancelada, caducada
    origen TEXT,
    creado TEXT NOT NULL,
    caduca TEXT NOT NULL,
    resuelto TEXT,
    resultado TEXT
);
CREATE TABLE IF NOT EXISTS auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    origen TEXT,
    evento TEXT NOT NULL,
    conexion_id TEXT,
    plataforma TEXT,
    detalle TEXT,
    ok INTEGER NOT NULL
);
"""


def ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Almacen:
    def __init__(self, ruta: str, clave_cifrado: str):
        if not clave_cifrado:
            raise RuntimeError("Falta CLAVE_CIFRADO. Genera una con: python -m app.cli generar-clave")
        self._fernet = Fernet(clave_cifrado.encode())
        if ruta != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(ruta)), exist_ok=True)
        self._con = sqlite3.connect(ruta, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._con.executescript(ESQUEMA)
            self._con.execute("PRAGMA foreign_keys = ON")
            self._con.commit()

    # --- utilidades ---
    def _cifrar(self, datos: dict) -> bytes:
        return self._fernet.encrypt(json.dumps(datos).encode())

    def _descifrar(self, blob: bytes) -> dict:
        try:
            return json.loads(self._fernet.decrypt(blob))
        except InvalidToken as e:
            raise RuntimeError("No se pueden descifrar las credenciales: ¿ha cambiado CLAVE_CIFRADO?") from e

    def _q(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._con.execute(sql, params)
            filas = cur.fetchall()
            self._con.commit()
            return filas

    # --- credenciales ---
    def guardar_credencial(self, login: str, datos: dict, etiqueta: str | None = None,
                           caduca: str | None = None, credencial_id: str | None = None) -> str:
        cid = credencial_id or uuid.uuid4().hex[:12]
        t = ahora()
        existe = self._q("SELECT id FROM credenciales WHERE id=?", (cid,))
        if existe:
            self._q("UPDATE credenciales SET datos=?, caduca=?, actualizado=?, etiqueta=COALESCE(?, etiqueta) WHERE id=?",
                    (self._cifrar(datos), caduca, t, etiqueta, cid))
        else:
            self._q("INSERT INTO credenciales VALUES (?,?,?,?,?,?,?)",
                    (cid, login, etiqueta, self._cifrar(datos), caduca, t, t))
        return cid

    def credencial(self, cid: str) -> dict | None:
        f = self._q("SELECT * FROM credenciales WHERE id=?", (cid,))
        if not f:
            return None
        r = dict(f[0])
        r["datos"] = self._descifrar(r["datos"])
        return r

    def listar_credenciales(self) -> list[dict]:
        return [{k: r[k] for k in ("id", "login", "etiqueta", "caduca", "creado", "actualizado")}
                for r in self._q("SELECT * FROM credenciales ORDER BY creado DESC")]

    def borrar_credencial(self, cid: str) -> None:
        self._q("DELETE FROM conexiones WHERE credencial_id=?", (cid,))
        self._q("DELETE FROM credenciales WHERE id=?", (cid,))

    # --- conexiones ---
    def vincular(self, cliente: str, plataforma: str, cuenta_id: str, nombre: str | None,
                 credencial_id: str, extra: dict | None = None) -> str:
        existente = self._q("SELECT id FROM conexiones WHERE cliente=? AND plataforma=? AND cuenta_id=?",
                            (cliente, plataforma, cuenta_id))
        if existente:
            cid = existente[0]["id"]
            self._q("UPDATE conexiones SET nombre=?, credencial_id=?, extra=? WHERE id=?",
                    (nombre, credencial_id, json.dumps(extra or {}), cid))
            return cid
        cid = uuid.uuid4().hex[:12]
        self._q("INSERT INTO conexiones VALUES (?,?,?,?,?,?,?,?)",
                (cid, cliente, plataforma, cuenta_id, nombre, credencial_id, json.dumps(extra or {}), ahora()))
        return cid

    def conexion(self, cid: str) -> dict | None:
        f = self._q("SELECT * FROM conexiones WHERE id=?", (cid,))
        if not f:
            return None
        r = dict(f[0])
        r["extra"] = json.loads(r["extra"])
        return r

    def listar_conexiones(self, cliente: str | None = None) -> list[dict]:
        if cliente:
            filas = self._q("SELECT * FROM conexiones WHERE cliente=? ORDER BY plataforma", (cliente,))
        else:
            filas = self._q("SELECT * FROM conexiones ORDER BY cliente, plataforma")
        salida = []
        for r in filas:
            d = dict(r)
            d["extra"] = json.loads(d["extra"])
            salida.append(d)
        return salida

    def desvincular(self, cid: str) -> None:
        self._q("DELETE FROM conexiones WHERE id=?", (cid,))

    # --- estados OAuth (un solo uso) ---
    def crear_estado(self, login: str, extra: dict | None = None) -> str:
        state = uuid.uuid4().hex + uuid.uuid4().hex
        self._q("INSERT INTO estados_oauth VALUES (?,?,?,?)", (state, login, json.dumps(extra or {}), ahora()))
        return state

    def consumir_estado(self, state: str, max_min: int = 20) -> dict | None:
        f = self._q("SELECT * FROM estados_oauth WHERE state=?", (state,))
        self._q("DELETE FROM estados_oauth WHERE state=?", (state,))
        if not f:
            return None
        r = dict(f[0])
        creado = datetime.fromisoformat(r["creado"])
        if (datetime.now(timezone.utc) - creado).total_seconds() > max_min * 60:
            return None
        r["extra"] = json.loads(r["extra"])
        return r

    # --- acciones pendientes ---
    def crear_accion(self, token: str, conexion_id: str, accion: str, parametros: dict,
                     vista_previa: dict, origen: str, caduca: str) -> None:
        self._q("INSERT INTO acciones VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (token, conexion_id, accion, json.dumps(parametros), json.dumps(vista_previa, default=str),
                 "pendiente", origen, ahora(), caduca, None, None))

    def accion(self, token: str) -> dict | None:
        f = self._q("SELECT * FROM acciones WHERE token=?", (token,))
        if not f:
            return None
        r = dict(f[0])
        for k in ("parametros", "vista_previa", "resultado"):
            if r.get(k):
                r[k] = json.loads(r[k])
        return r

    def resolver_accion(self, token: str, estado: str, resultado: Any = None) -> bool:
        """Cambia el estado solo si sigue pendiente (evita dobles ejecuciones)."""
        with self._lock:
            cur = self._con.execute(
                "UPDATE acciones SET estado=?, resuelto=?, resultado=? WHERE token=? AND estado='pendiente'",
                (estado, ahora(), json.dumps(resultado, default=str), token))
            self._con.commit()
            return cur.rowcount == 1

    def actualizar_resultado(self, token: str, estado: str, resultado: Any) -> None:
        self._q("UPDATE acciones SET estado=?, resultado=? WHERE token=?",
                (estado, json.dumps(resultado, default=str), token))

    def listar_acciones(self, estado: str | None = None, limite: int = 50) -> list[dict]:
        if estado:
            filas = self._q("SELECT * FROM acciones WHERE estado=? ORDER BY creado DESC LIMIT ?", (estado, limite))
        else:
            filas = self._q("SELECT * FROM acciones ORDER BY creado DESC LIMIT ?", (limite,))
        salida = []
        for r in filas:
            d = dict(r)
            for k in ("parametros", "vista_previa", "resultado"):
                if d.get(k):
                    d[k] = json.loads(d[k])
            salida.append(d)
        return salida

    # --- auditoría ---
    def auditar(self, evento: str, ok: bool, origen: str | None = None, conexion_id: str | None = None,
                plataforma: str | None = None, detalle: Any = None) -> None:
        self._q("INSERT INTO auditoria (fecha, origen, evento, conexion_id, plataforma, detalle, ok) VALUES (?,?,?,?,?,?,?)",
                (ahora(), origen, evento, conexion_id, plataforma, json.dumps(detalle, default=str)[:20000], int(ok)))

    def listar_auditoria(self, limite: int = 100) -> list[dict]:
        filas = self._q("SELECT * FROM auditoria ORDER BY id DESC LIMIT ?", (limite,))
        salida = []
        for r in filas:
            d = dict(r)
            d["detalle"] = json.loads(d["detalle"]) if d["detalle"] else None
            d["ok"] = bool(d["ok"])
            salida.append(d)
        return salida
