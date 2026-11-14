"""Shopify: GraphQL Admin API. Apps del Dev Dashboard (OAuth o client credentials)."""
from __future__ import annotations

import hashlib
import hmac
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from ..config import Settings
from ..http import ErrorPlataforma, ErrorUsuario, json_o_error, peticion
from .base import Accion, Contexto, Login, Plataforma, caduca_en, exigir, num, opcion

PATRON_TIENDA = re.compile(r"^[a-z0-9][a-z0-9\-]*\.myshopify\.com$")


def tienda_valida(valor: str) -> str:
    t = (valor or "").strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")
    if "." not in t:
        t += ".myshopify.com"
    if not PATRON_TIENDA.match(t):
        raise ErrorUsuario("La tienda debe ser del tipo mitienda.myshopify.com")
    return t


def hmac_valido(params: dict, secreto: str) -> bool:
    recibido = params.get("hmac", "")
    mensaje = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if k not in ("hmac", "signature"))
    calculado = hmac.new(secreto.encode(), mensaje.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(calculado, recibido)


async def gql(ctx: Contexto, consulta: str, variables: dict | None = None) -> dict:
    tienda = ctx.datos["tienda"]
    r = await peticion(ctx.http, "shopify", "POST",
                       f"https://{tienda}/admin/api/{ctx.ajustes.shopify_version}/graphql.json",
                       json={"query": consulta, "variables": variables or {}},
                       headers={"X-Shopify-Access-Token": ctx.datos["access_token"]})
    cuerpo = json_o_error(r, "shopify")
    if cuerpo.get("errors"):
        raise ErrorPlataforma("shopify", str(cuerpo["errors"])[:400], detalle=cuerpo["errors"])
    return cuerpo.get("data") or {}


def _errores_usuario(res: dict) -> None:
    errores = res.get("userErrors") or []
    if errores:
        raise ErrorUsuario("Shopify: " + "; ".join(e.get("message", "") for e in errores))


class LoginShopify(Login):
    id = "shopify"
    nombre = "Shopify"
    plataformas = ("shopify",)
    pide_tienda = True
    campos_manuales = {"tienda": "mitienda.myshopify.com",
                       "client_id": "Client ID de la app del Dev Dashboard instalada en la tienda",
                       "client_secret": "Client secret de esa app",
                       "access_token": "Token de una app personalizada antigua (shpat_…), si aún la tienes"}
    campos_opcionales = frozenset({"client_id", "client_secret", "access_token"})

    def configurado(self, a):
        return bool(a.shopify_client_id and a.shopify_client_secret)

    def url_autorizacion(self, a, state, redirect_uri, extra):
        tienda = tienda_valida(extra.get("tienda", ""))
        return f"https://{tienda}/admin/oauth/authorize?" + urlencode({
            "client_id": a.shopify_client_id, "scope": a.shopify_scopes, "redirect_uri": redirect_uri,
            "state": state})

    async def canjear_codigo(self, http, a, params, redirect_uri, extra):
        exigir(params, "code", "shop", "hmac")
        if not hmac_valido(params, a.shopify_client_secret):
            raise ErrorUsuario("La firma HMAC de Shopify no es válida")
        tienda = tienda_valida(params["shop"])
        if tienda != tienda_valida(extra.get("tienda", tienda)):
            raise ErrorUsuario("La tienda de vuelta no coincide con la que iniciaste")
        r = await peticion(http, "shopify", "POST", f"https://{tienda}/admin/oauth/access_token", intentos=1,
                           json={"client_id": a.shopify_client_id, "client_secret": a.shopify_client_secret,
                                 "code": params["code"]})
        t = json_o_error(r, "shopify")
        return {"tienda": tienda, "access_token": t["access_token"], "refresh_token": t.get("refresh_token"),
                "caduca": caduca_en(t.get("expires_in")), "modo": "oauth"}

    async def preparar_manual(self, http, a, datos):
        limpio = await super().preparar_manual(http, a, datos)
        limpio["tienda"] = tienda_valida(limpio["tienda"])
        if limpio.get("access_token"):
            return {"tienda": limpio["tienda"], "access_token": limpio["access_token"], "modo": "token"}
        if not (limpio.get("client_id") and limpio.get("client_secret")):
            raise ErrorUsuario("Indica client_id y client_secret (o un access_token antiguo)")
        datos_cc = {**limpio, "modo": "client_credentials"}
        return await self._client_credentials(http, datos_cc)

    async def _client_credentials(self, http, datos):
        r = await peticion(http, "shopify", "POST", f"https://{datos['tienda']}/admin/oauth/access_token",
                           intentos=1, data={"grant_type": "client_credentials", "client_id": datos["client_id"],
                                             "client_secret": datos["client_secret"]})
        t = json_o_error(r, "shopify")
        return {**datos, "access_token": t["access_token"], "caduca": caduca_en(t.get("expires_in"))}

    def necesita_refresco(self, datos):
        if datos.get("modo") == "client_credentials" or datos.get("refresh_token"):
            return super().necesita_refresco(datos)
        return False

    async def refrescar(self, http, a, datos):
        if datos.get("modo") == "client_credentials":
            return await self._client_credentials(http, datos)
        r = await peticion(http, "shopify", "POST", f"https://{datos['tienda']}/admin/oauth/access_token",
                           data={"grant_type": "refresh_token", "refresh_token": datos["refresh_token"],
                                 "client_id": a.shopify_client_id, "client_secret": a.shopify_client_secret})
        t = json_o_error(r, "shopify")
        return {**datos, "access_token": t["access_token"], "refresh_token": t.get("refresh_token", datos["refresh_token"]),
                "caduca": caduca_en(t.get("expires_in"))}

    async def listar_cuentas(self, http, a, datos):
        d = await gql(Contexto(http, a, datos), "{ shop { name myshopifyDomain currencyCode ianaTimezone } }")
        s = d.get("shop", {})
        return [{"plataforma": "shopify", "cuenta_id": s.get("myshopifyDomain") or datos["tienda"],
                 "nombre": s.get("name"), "extra": {"moneda": s.get("currencyCode"), "zona": s.get("ianaTimezone")}}]


Q_PEDIDOS = """
query($q: String!, $c: String) {
  orders(first: 250, after: $c, query: $q, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      createdAt test cancelledAt
      currentTotalPriceSet { shopMoney { amount currencyCode } }
      currentSubtotalPriceSet { shopMoney { amount } }
      currentTotalTaxSet { shopMoney { amount } }
      totalRefundedSet { shopMoney { amount } }
    }
  }
}"""


class Shopify(Plataforma):
    id = "shopify"
    nombre = "Shopify"
    tipo = "ecommerce"
    login = "shopify"

    async def metricas(self, ctx, desde: date, hasta: date, opciones):
        tienda = (await gql(ctx, "{ shop { ianaTimezone currencyCode } }")).get("shop", {})
        zona = ZoneInfo(tienda.get("ianaTimezone") or "UTC")
        # margen de un día por la zona horaria; luego se filtra por día local de la tienda
        consulta = (f"created_at:>={(desde - timedelta(days=1)).isoformat()} "
                    f"created_at:<={(hasta + timedelta(days=1)).isoformat()}")
        por_dia: dict[str, dict] = defaultdict(lambda: {"pedidos": 0, "cancelados": 0, "ventas_totales": 0.0,
                                                       "subtotal": 0.0, "impuestos": 0.0, "reembolsado": 0.0})
        cursor, paginas = None, 0
        while True:
            d = (await gql(ctx, Q_PEDIDOS, {"q": consulta, "c": cursor}))["orders"]
            for o in d["nodes"]:
                if o.get("test"):
                    continue
                dia = datetime.fromisoformat(o["createdAt"].replace("Z", "+00:00")).astimezone(zona).date()
                if not desde <= dia <= hasta:
                    continue
                f = por_dia[dia.isoformat()]
                if o.get("cancelledAt"):
                    f["cancelados"] += 1
                    continue
                f["pedidos"] += 1
                f["ventas_totales"] += num(o["currentTotalPriceSet"]["shopMoney"]["amount"])
                f["subtotal"] += num(o["currentSubtotalPriceSet"]["shopMoney"]["amount"])
                f["impuestos"] += num(o["currentTotalTaxSet"]["shopMoney"]["amount"])
                f["reembolsado"] += num(o["totalRefundedSet"]["shopMoney"]["amount"])
            paginas += 1
            if not d["pageInfo"]["hasNextPage"] or paginas >= 400:
                break
            cursor = d["pageInfo"]["endCursor"]
        filas = [{"fecha": k, "plataforma": "shopify", "cuenta_id": ctx.cuenta_id,
                  **{m: (round(v, 2) if isinstance(v, float) else v) for m, v in f.items()},
                  "moneda": tienda.get("currencyCode")} for k, f in sorted(por_dia.items())]
        notas = [f"Días en la zona horaria de la tienda ({zona.key}). Sin pedidos de prueba.",
                 "ventas_totales = total actual (con impuestos y envío, tras ediciones y devoluciones); "
                 "subtotal = tras descuentos, sin impuestos ni envío.",
                 "Sin el permiso read_all_orders, Shopify solo devuelve los últimos 60 días."]
        return {"filas": filas, "notas": notas}

    async def contenido(self, ctx, opciones):
        d = await gql(ctx, """query($n:Int!){ products(first:$n, sortKey: UPDATED_AT, reverse: true) {
            nodes { id title status handle totalInventory updatedAt } } }""", {"n": int(opciones.get("limite", 50))})
        return {"tipo": "productos", "elementos": d["products"]["nodes"]}

    def acciones(self):
        return {
            "cambiar_estado_producto": Accion(
                "cambiar_estado_producto", "gestionar", "Cambia el estado de un producto (activo, borrador, archivado).",
                {"producto_id": "ID del producto (gid://shopify/Product/… o el número)",
                 "estado": "activo | borrador | archivado"}, self._prev_estado, self._ejec_estado),
            "crear_producto_borrador": Accion(
                "crear_producto_borrador", "publicar", "Crea un producto en borrador (no visible en la tienda).",
                {"titulo": "nombre del producto", "descripcion_html": "descripción (HTML permitido)"},
                self._prev_crear, self._ejec_crear),
        }

    async def _prev_estado(self, ctx, p):
        exigir(p, "producto_id", "estado")
        estado = opcion(p["estado"], {"activo": "ACTIVE", "active": "ACTIVE", "borrador": "DRAFT", "draft": "DRAFT",
                                      "archivado": "ARCHIVED", "archived": "ARCHIVED"}, "estado")
        gid = str(p["producto_id"])
        if gid.isdigit():
            gid = f"gid://shopify/Product/{gid}"
        prod = (await gql(ctx, "query($id: ID!){ product(id:$id){ id title status } }", {"id": gid})).get("product")
        if not prod:
            raise ErrorUsuario("No existe ese producto en la tienda")
        avisos = ["Activo = visible y comprable en la tienda."] if estado == "ACTIVE" else []
        return {"resumen": f"Shopify · '{prod['title']}': {prod['status']} → {estado}", "avisos": avisos,
                "_params": {"id": gid, "estado": estado}}

    async def _ejec_estado(self, ctx, p):
        d = await gql(ctx, """mutation($p: ProductUpdateInput!){ productUpdate(product:$p){
            product { id status } userErrors { field message } } }""", {"p": {"id": p["id"], "status": p["estado"]}})
        _errores_usuario(d["productUpdate"])
        return {"ok": True, "producto": d["productUpdate"]["product"]}

    async def _prev_crear(self, ctx, p):
        exigir(p, "titulo")
        return {"resumen": f"Shopify · {ctx.cuenta_id}: crear producto en borrador '{p['titulo']}'",
                "contenido": {"titulo": p["titulo"], "descripcion_html": p.get("descripcion_html", "")},
                "_params": {"titulo": p["titulo"], "descripcion_html": p.get("descripcion_html", "")}}

    async def _ejec_crear(self, ctx, p):
        d = await gql(ctx, """mutation($p: ProductCreateInput!){ productCreate(product:$p){
            product { id title status } userErrors { field message } } }""",
                      {"p": {"title": p["titulo"], "descriptionHtml": p["descripcion_html"], "status": "DRAFT"}})
        _errores_usuario(d["productCreate"])
        return {"ok": True, "producto": d["productCreate"]["product"]}
