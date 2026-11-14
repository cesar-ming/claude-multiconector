"""Lógica central: conexiones, refresco de tokens, lectura de datos y acciones con confirmación."""
from __future__ import annotations

import secrets
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from .config import Settings
from .db import Almacen
from .http import ErrorPlataforma, ErrorUsuario, crear_cliente
from .proveedores.base import Contexto
from .proveedores.registro import LOGINS, PLATAFORMAS

MAX_DIAS_RANGO = 730


def fecha(valor: Any, nombre: str) -> date:
    try:
        return date.fromisoformat(str(valor))
    except ValueError:
        raise ErrorUsuario(f"{nombre} debe tener formato AAAA-MM-DD") from None


class Servicio:
    def __init__(self, ajustes: Settings, almacen: Almacen | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.a = ajustes
        self.db = almacen or Almacen(ajustes.db_path, ajustes.clave_cifrado)
        self.http = crear_cliente(transport)

    async def cerrar(self) -> None:
        await self.http.aclose()

    def redirect_uri(self, login_id: str) -> str:
        return f"{self.a.base_url.rstrip('/')}/oauth/callback/{login_id}"

    # ------------------------------------------------------------ catálogo

    def catalogo(self) -> dict:
        logins = [{"id": l.id, "nombre": l.nombre, "plataformas": list(l.plataformas), "oauth": l.oauth,
                   "configurado": l.configurado(self.a), "pide_tienda": l.pide_tienda,
                   "campos_manuales": l.campos_manuales, "campos_opcionales": sorted(l.campos_opcionales),
                   "redirect_uri": self.redirect_uri(l.id) if l.oauth else None} for l in LOGINS.values()]
        plataformas = [{"id": p.id, "nombre": p.nombre, "tipo": p.tipo, "login": p.login,
                        "acciones": [{"nombre": x.nombre, "tipo": x.tipo, "descripcion": x.descripcion,
                                      "parametros": x.parametros} for x in p.acciones().values()]}
                       for p in PLATAFORMAS.values()]
        return {"logins": logins, "plataformas": plataformas,
                "salvaguardas": {"permitir_publicar": self.a.permitir_publicar,
                                 "permitir_gestionar": self.a.permitir_gestionar,
                                 "max_subida_presupuesto_pct": self.a.max_subida_presupuesto_pct,
                                 "max_presupuesto_diario": self.a.max_presupuesto_diario,
                                 "caducidad_accion_min": self.a.caducidad_accion_min}}

    # ------------------------------------------------------------ OAuth y credenciales

    def enlace_oauth(self, login_id: str, tienda: str | None = None, etiqueta: str | None = None) -> str:
        login = self._login(login_id)
        if not login.oauth:
            raise ErrorUsuario(f"{login.nombre} se conecta con credenciales manuales, no con OAuth")
        if not login.configurado(self.a):
            raise ErrorUsuario(f"Faltan las credenciales de la app de {login.nombre} en el .env")
        extra = {"etiqueta": etiqueta}
        if login.pide_tienda:
            if not tienda:
                raise ErrorUsuario("Indica la tienda (mitienda.myshopify.com)")
            extra["tienda"] = tienda
        state = self.db.crear_estado(login_id, extra)
        return login.url_autorizacion(self.a, state, self.redirect_uri(login_id), extra)

    async def completar_oauth(self, login_id: str, params: dict) -> str:
        estado = self.db.consumir_estado(params.get("state", ""))
        if not estado or estado["login"] != login_id:
            raise ErrorUsuario("Enlace de conexión caducado o no válido. Vuelve a empezar desde el panel.")
        login = self._login(login_id)
        try:
            datos = await login.canjear_codigo(self.http, self.a, params, self.redirect_uri(login_id), estado["extra"])
        except (ErrorPlataforma, ErrorUsuario) as e:
            self.db.auditar("oauth", False, "navegador", plataforma=login_id, detalle=str(e))
            raise
        cid = self.db.guardar_credencial(login_id, datos, estado["extra"].get("etiqueta"), datos.get("caduca"))
        self.db.auditar("oauth", True, "navegador", plataforma=login_id, detalle={"credencial_id": cid})
        return cid

    async def credencial_manual(self, login_id: str, datos: dict, etiqueta: str | None, origen: str) -> str:
        login = self._login(login_id)
        preparado = await login.preparar_manual(self.http, self.a, datos)
        cid = self.db.guardar_credencial(login_id, preparado, etiqueta, preparado.get("caduca"))
        self.db.auditar("credencial_manual", True, origen, plataforma=login_id, detalle={"credencial_id": cid})
        return cid

    async def _datos_frescos(self, credencial_id: str) -> tuple[dict, Any]:
        cred = self.db.credencial(credencial_id)
        if not cred:
            raise ErrorUsuario("La credencial ya no existe: vuelve a conectar la cuenta")
        login = self._login(cred["login"])
        datos = cred["datos"]
        if login.necesita_refresco(datos):
            nuevos = await login.refrescar(self.http, self.a, datos)
            if nuevos:
                self.db.guardar_credencial(cred["login"], nuevos, caduca=nuevos.get("caduca"), credencial_id=cred["id"])
                datos = nuevos
        elif datos.get("caduca") and datetime.fromisoformat(datos["caduca"]) < datetime.now(timezone.utc):
            raise ErrorUsuario(f"El acceso de {login.nombre} ha caducado: vuelve a conectar desde el panel")
        return datos, login

    async def cuentas_disponibles(self, credencial_id: str) -> list[dict]:
        datos, login = await self._datos_frescos(credencial_id)
        cuentas = await login.listar_cuentas(self.http, self.a, datos)
        vinculadas = {(c["plataforma"], c["cuenta_id"]): c["cliente"] for c in self.db.listar_conexiones()
                      if c["credencial_id"] == credencial_id}
        for c in cuentas:
            c["vinculada_a"] = vinculadas.get((c["plataforma"], c["cuenta_id"]))
        return cuentas

    async def vincular(self, credencial_id: str, cliente: str, plataforma: str, cuenta_id: str, origen: str) -> dict:
        cliente = _slug(cliente)
        disponibles = await self.cuentas_disponibles(credencial_id)
        cuenta = next((c for c in disponibles if c["plataforma"] == plataforma and str(c["cuenta_id"]) == str(cuenta_id)),
                      None)
        if not cuenta:
            raise ErrorUsuario("Esa cuenta no está entre las accesibles con esta credencial")
        cid = self.db.vincular(cliente, plataforma, str(cuenta_id), cuenta.get("nombre"), credencial_id,
                               cuenta.get("extra"))
        self.db.auditar("vincular", True, origen, cid, plataforma, {"cliente": cliente, "cuenta_id": cuenta_id})
        return self.db.conexion(cid)

    # ------------------------------------------------------------ lectura

    async def _contexto(self, conexion_id: str) -> tuple[dict, Contexto]:
        con = self.db.conexion(conexion_id)
        if not con:
            raise ErrorUsuario(f"No existe la conexión {conexion_id}. Consulta la lista de conexiones.")
        datos, _ = await self._datos_frescos(con["credencial_id"])
        return con, Contexto(self.http, self.a, datos, con["cuenta_id"], con["extra"])

    async def metricas(self, conexion_id: str, desde: str, hasta: str, opciones: dict | None = None) -> dict:
        d, h = fecha(desde, "desde"), fecha(hasta, "hasta")
        if h < d:
            raise ErrorUsuario("'hasta' es anterior a 'desde'")
        if (h - d).days > MAX_DIAS_RANGO:
            raise ErrorUsuario(f"Rango máximo: {MAX_DIAS_RANGO} días")
        con, ctx = await self._contexto(conexion_id)
        res = await PLATAFORMAS[con["plataforma"]].metricas(ctx, d, h, opciones or {})
        return {"conexion_id": conexion_id, "cliente": con["cliente"], "plataforma": con["plataforma"],
                "cuenta": con["nombre"], "desde": d.isoformat(), "hasta": h.isoformat(),
                "obtenido": datetime.now(timezone.utc).isoformat(timespec="seconds"), **res}

    async def contenido(self, conexion_id: str, opciones: dict | None = None) -> dict:
        con, ctx = await self._contexto(conexion_id)
        res = await PLATAFORMAS[con["plataforma"]].contenido(ctx, opciones or {})
        return {"conexion_id": conexion_id, "plataforma": con["plataforma"], "cuenta": con["nombre"], **res}

    async def resumen_cliente(self, cliente: str, desde: str, hasta: str) -> dict:
        """Junta publicidad y Shopify de un cliente. Solo suma datos reales; lo que falla se indica aparte."""
        conexiones = [c for c in self.db.listar_conexiones(_slug(cliente))
                      if PLATAFORMAS[c["plataforma"]].tipo in ("ads", "ecommerce")]
        if not conexiones:
            raise ErrorUsuario(f"El cliente '{cliente}' no tiene cuentas de publicidad ni de Shopify conectadas")
        por_plataforma: dict[str, dict] = {}
        errores, monedas = {}, set()
        ventas = None
        for c in conexiones:
            try:
                res = await self.metricas(c["id"], desde, hasta)
            except (ErrorPlataforma, ErrorUsuario) as e:
                errores[f"{c['plataforma']}:{c['nombre'] or c['cuenta_id']}"] = str(e)
                continue
            if c["plataforma"] == "shopify":
                ventas = ventas or {"pedidos": 0, "ventas_totales": 0.0, "subtotal": 0.0, "moneda": None}
                for f in res["filas"]:
                    ventas["pedidos"] += f["pedidos"]
                    ventas["ventas_totales"] += f["ventas_totales"]
                    ventas["subtotal"] += f["subtotal"]
                    ventas["moneda"] = f["moneda"]
                    monedas.add(f["moneda"])
                continue
            t = por_plataforma.setdefault(c["plataforma"], defaultdict(float))
            hay_ingresos = False
            for f in res["filas"]:
                for k in ("inversion", "impresiones", "clics"):
                    t[k] += f[k]
                if f["conversiones"] is not None:
                    t["conversiones"] += f["conversiones"]
                if f["ingresos"] is not None:
                    t["ingresos_atribuidos"] += f["ingresos"]
                    hay_ingresos = True
                if f.get("moneda"):
                    monedas.add(f["moneda"])
            t["_con_ingresos"] = t.get("_con_ingresos", 0) or float(hay_ingresos)
        salida_plat = {}
        inversion_total = 0.0
        for p, t in por_plataforma.items():
            inv = round(t["inversion"], 2)
            inversion_total += inv
            fila = {"inversion": inv, "impresiones": int(t["impresiones"]), "clics": int(t["clics"]),
                    "conversiones": round(t["conversiones"], 2)}
            if t.get("_con_ingresos"):
                fila["ingresos_atribuidos"] = round(t["ingresos_atribuidos"], 2)
                fila["roas"] = round(t["ingresos_atribuidos"] / inv, 2) if inv else None
            else:
                fila["ingresos_atribuidos"] = None
            fila["cpa"] = round(inv / t["conversiones"], 2) if t["conversiones"] else None
            salida_plat[p] = fila
        resumen = {"cliente": _slug(cliente), "desde": desde, "hasta": hasta, "por_plataforma": salida_plat,
                   "inversion_total": round(inversion_total, 2), "errores": errores, "notas": []}
        if ventas:
            ventas = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in ventas.items()}
            resumen["shopify"] = ventas
        if len({m for m in monedas if m}) > 1:
            resumen["notas"].append(f"Hay varias monedas ({', '.join(sorted(m for m in monedas if m))}): "
                                    "no se calcula MER porque las sumas no son comparables.")
        elif ventas and inversion_total and not errores:
            resumen["mer"] = round(ventas["ventas_totales"] / inversion_total, 2)
            resumen["notas"].append("MER = ventas_totales de Shopify / inversión total en medios.")
        elif errores:
            resumen["notas"].append("No se calcula MER porque alguna fuente ha fallado.")
        resumen["notas"].append("Los ingresos atribuidos de cada plataforma no se suman entre sí (doble atribución).")
        return resumen

    # ------------------------------------------------------------ acciones con confirmación

    async def proponer(self, conexion_id: str, accion: str, parametros: dict, origen: str) -> dict:
        con, ctx = await self._contexto(conexion_id)
        plat = PLATAFORMAS[con["plataforma"]]
        acc = plat.acciones().get(accion)
        if not acc:
            raise ErrorUsuario(f"{plat.nombre} no tiene la acción '{accion}'. Disponibles: "
                               f"{', '.join(plat.acciones()) or 'ninguna'}")
        self._comprobar_permiso(acc.tipo)
        vista = await acc.previsualizar(ctx, dict(parametros or {}))
        params_ejec = vista.pop("_params")
        self._comprobar_limites(vista)
        token = secrets.token_urlsafe(12)
        caduca = (datetime.now(timezone.utc) + timedelta(minutes=self.a.caducidad_accion_min)).isoformat(timespec="seconds")
        self.db.crear_accion(token, conexion_id, accion, params_ejec, vista, origen, caduca)
        self.db.auditar("propuesta", True, origen, conexion_id, con["plataforma"],
                        {"accion": accion, "token": token, "resumen": vista.get("resumen")})
        return {"token": token, "caduca": caduca, "cliente": con["cliente"], "plataforma": con["plataforma"],
                "cuenta": con["nombre"], "accion": accion, **vista,
                "siguiente_paso": "Revisa la vista previa. Para ejecutarla, confirma con este token."}

    async def confirmar(self, token: str, origen: str) -> dict:
        acc = self.db.accion(token)
        if not acc:
            raise ErrorUsuario("Token desconocido")
        if acc["estado"] != "pendiente":
            raise ErrorUsuario(f"Esta acción ya está {acc['estado']}")
        if datetime.fromisoformat(acc["caduca"]) < datetime.now(timezone.utc):
            self.db.resolver_accion(token, "caducada")
            raise ErrorUsuario("La acción ha caducado: vuelve a proponerla para ver el estado actual")
        con, ctx = await self._contexto(acc["conexion_id"])
        definicion = PLATAFORMAS[con["plataforma"]].acciones()[acc["accion"]]
        self._comprobar_permiso(definicion.tipo)
        if not self.db.resolver_accion(token, "en_curso"):
            raise ErrorUsuario("Otra petición está ejecutando esta acción")
        try:
            resultado = await definicion.ejecutar(ctx, acc["parametros"])
        except (ErrorPlataforma, ErrorUsuario) as e:
            self.db.actualizar_resultado(token, "fallida", {"error": str(e)})
            self.db.auditar("ejecucion", False, origen, con["id"], con["plataforma"],
                            {"accion": acc["accion"], "token": token, "error": str(e)})
            raise
        self.db.actualizar_resultado(token, "ejecutada", resultado)
        self.db.auditar("ejecucion", True, origen, con["id"], con["plataforma"],
                        {"accion": acc["accion"], "token": token, "resumen": acc["vista_previa"].get("resumen"),
                         "resultado": resultado})
        return {"ok": True, "resumen": acc["vista_previa"].get("resumen"), "resultado": resultado}

    def cancelar(self, token: str, origen: str) -> dict:
        if not self.db.resolver_accion(token, "cancelada"):
            raise ErrorUsuario("No hay ninguna acción pendiente con ese token")
        self.db.auditar("cancelacion", True, origen, detalle={"token": token})
        return {"ok": True, "cancelada": token}

    def _comprobar_permiso(self, tipo: str) -> None:
        if tipo == "publicar" and not self.a.permitir_publicar:
            raise ErrorUsuario("Publicar está desactivado (PERMITIR_PUBLICAR=false en el .env)")
        if tipo == "gestionar" and not self.a.permitir_gestionar:
            raise ErrorUsuario("Gestionar campañas está desactivado (PERMITIR_GESTIONAR=false en el .env)")

    def _comprobar_limites(self, vista: dict) -> None:
        cambio = vista.get("cambio_presupuesto")
        if not cambio:
            return
        nuevo, actual = cambio["nuevo"], cambio.get("actual")
        if nuevo > self.a.max_presupuesto_diario:
            raise ErrorUsuario(f"{nuevo} supera el tope de presupuesto diario ({self.a.max_presupuesto_diario}). "
                               "Súbelo en MAX_PRESUPUESTO_DIARIO si de verdad lo necesitas.")
        if actual and nuevo > actual * (1 + self.a.max_subida_presupuesto_pct / 100):
            raise ErrorUsuario(f"La subida ({actual} → {nuevo}) supera el {self.a.max_subida_presupuesto_pct:.0f} % "
                               "permitido por acción. Hazlo en varios pasos o ajusta MAX_SUBIDA_PRESUPUESTO_PCT.")

    def _login(self, login_id: str):
        if login_id not in LOGINS:
            raise ErrorUsuario(f"Login desconocido: {login_id}. Opciones: {', '.join(LOGINS)}")
        return LOGINS[login_id]


def _slug(texto: str) -> str:
    import re
    import unicodedata
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    if not t:
        raise ErrorUsuario("Indica el cliente (p. ej. echaleku)")
    return t
