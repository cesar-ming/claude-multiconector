"""Interfaz común de todas las plataformas."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import httpx

from ..config import Settings
from ..http import ErrorUsuario


@dataclass
class Contexto:
    """Lo que necesita un proveedor para trabajar con una cuenta concreta."""
    http: httpx.AsyncClient
    ajustes: Settings
    datos: dict  # credenciales descifradas del login
    cuenta_id: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Accion:
    nombre: str
    tipo: str  # "publicar" o "gestionar"
    descripcion: str
    parametros: dict[str, str]  # nombre -> descripción
    previsualizar: Callable[[Contexto, dict], Awaitable[dict]]
    ejecutar: Callable[[Contexto, dict], Awaitable[dict]]


class Login:
    """Forma de obtener credenciales (un login puede dar acceso a varias plataformas)."""
    id: str = ""
    nombre: str = ""
    plataformas: tuple[str, ...] = ()
    oauth: bool = True
    campos_manuales: dict[str, str] = {}  # credenciales que se pueden introducir a mano (nombre -> ayuda)
    campos_opcionales: frozenset[str] = frozenset()
    pide_tienda: bool = False  # Shopify necesita el dominio de la tienda para iniciar OAuth

    def configurado(self, a: Settings) -> bool:
        return True

    def url_autorizacion(self, a: Settings, state: str, redirect_uri: str, extra: dict) -> str:
        raise ErrorUsuario(f"{self.nombre} no usa OAuth: introduce las credenciales a mano.")

    async def canjear_codigo(self, http: httpx.AsyncClient, a: Settings, params: dict, redirect_uri: str,
                             extra: dict) -> dict:
        raise NotImplementedError

    async def preparar_manual(self, http: httpx.AsyncClient, a: Settings, datos: dict) -> dict:
        """Valida y completa credenciales introducidas a mano."""
        if not self.campos_manuales:
            raise ErrorUsuario(f"{self.nombre} solo se conecta con OAuth.")
        limpio = {k: str(v).strip() for k, v in datos.items() if k in self.campos_manuales and v not in (None, "")}
        faltan = [c for c in self.campos_manuales if c not in self.campos_opcionales and not limpio.get(c)]
        if faltan:
            raise ErrorUsuario(f"Faltan campos: {', '.join(faltan)}")
        return limpio

    def necesita_refresco(self, datos: dict) -> bool:
        caduca = datos.get("caduca")
        if not caduca:
            return False
        return datetime.fromisoformat(caduca) - datetime.now(timezone.utc) < timedelta(minutes=5)

    async def refrescar(self, http: httpx.AsyncClient, a: Settings, datos: dict) -> dict | None:
        return None

    async def listar_cuentas(self, http: httpx.AsyncClient, a: Settings, datos: dict) -> list[dict]:
        """Devuelve [{plataforma, cuenta_id, nombre, extra}] de todas las cuentas accesibles."""
        raise NotImplementedError


class Plataforma:
    id: str = ""
    nombre: str = ""
    tipo: str = ""  # ads, social, ecommerce, cms
    login: str = ""

    async def metricas(self, ctx: Contexto, desde: date, hasta: date, opciones: dict) -> dict:
        raise NotImplementedError

    async def contenido(self, ctx: Contexto, opciones: dict) -> dict:
        raise NotImplementedError

    def acciones(self) -> dict[str, Accion]:
        return {}


# ---------- utilidades comunes ----------

def caduca_en(segundos: Any) -> str | None:
    try:
        s = int(segundos)
    except (TypeError, ValueError):
        return None
    if s <= 0:
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=s)).isoformat(timespec="seconds")


def tramos(desde: date, hasta: date, dias: int) -> list[tuple[date, date]]:
    """Parte un rango en tramos de como mucho `dias` días (límite de algunas APIs)."""
    salida, ini = [], desde
    while ini <= hasta:
        fin = min(ini + timedelta(days=dias - 1), hasta)
        salida.append((ini, fin))
        ini = fin + timedelta(days=1)
    return salida


def num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def num_o_none(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def exigir(params: dict, *nombres: str) -> None:
    faltan = [n for n in nombres if params.get(n) in (None, "")]
    if faltan:
        raise ErrorUsuario(f"Faltan parámetros: {', '.join(faltan)}")


def solo_digitos(valor: Any, nombre: str) -> str:
    v = str(valor).replace("-", "").strip()
    if not re.fullmatch(r"\d{1,25}", v):
        raise ErrorUsuario(f"{nombre} debe ser numérico (recibido: {valor!r})")
    return v


def importe_positivo(valor: Any, nombre: str = "importe") -> float:
    try:
        v = round(float(valor), 2)
    except (TypeError, ValueError):
        raise ErrorUsuario(f"{nombre} debe ser un número") from None
    if v <= 0:
        raise ErrorUsuario(f"{nombre} debe ser mayor que 0")
    return v


def opcion(valor: Any, permitidos: dict[str, str], nombre: str) -> str:
    """Normaliza un valor a uno de los permitidos (acepta alias en español)."""
    v = str(valor or "").strip().lower()
    if v in permitidos:
        return permitidos[v]
    raise ErrorUsuario(f"{nombre} no válido: {valor!r}. Opciones: {', '.join(sorted(set(permitidos)))}")


ESTADOS_ON_OFF = {"pausar": "off", "pausada": "off", "pausado": "off", "paused": "off", "off": "off",
                  "activar": "on", "activa": "on", "activo": "on", "active": "on", "enabled": "on",
                  "enable": "on", "on": "on"}


def fila_ads(fecha: str, plataforma: str, cuenta_id: str, campana_id: Any, campana: Any, inversion: Any,
             impresiones: Any, clics: Any, conversiones: Any, ingresos: Any, moneda: Any) -> dict:
    return {
        "fecha": fecha, "plataforma": plataforma, "cuenta_id": cuenta_id,
        "campana_id": str(campana_id) if campana_id is not None else None, "campana": campana,
        "inversion": round(num(inversion), 2), "impresiones": int(num(impresiones)), "clics": int(num(clics)),
        "conversiones": None if conversiones is None else round(num(conversiones), 2),
        "ingresos": None if ingresos is None else round(num(ingresos), 2),
        "moneda": moneda,
    }
