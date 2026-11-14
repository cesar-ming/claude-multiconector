"""Google Ads API (REST)."""
from __future__ import annotations

from datetime import date
from urllib.parse import urlencode

from ..config import Settings
from ..http import ErrorPlataforma, ErrorUsuario, json_o_error, peticion
from .base import (ESTADOS_ON_OFF, Accion, Contexto, Login, Plataforma, caduca_en, exigir, fila_ads,
                   importe_positivo, num, opcion, solo_digitos)

TOKEN_URL = "https://oauth2.googleapis.com/token"


def base(a: Settings) -> str:
    return f"https://googleads.googleapis.com/{a.google_ads_version}"


def cabeceras(a: Settings, token: str, login_customer_id: str | None = None) -> dict:
    h = {"Authorization": f"Bearer {token}", "developer-token": a.google_developer_token}
    lc = (login_customer_id or a.google_login_customer_id or "").replace("-", "")
    if lc:
        h["login-customer-id"] = lc
    return h


async def gaql(ctx_http, a: Settings, token: str, cliente: str, consulta: str,
               login_customer_id: str | None = None) -> list[dict]:
    filas, pagina = [], None
    while True:
        cuerpo = {"query": consulta}
        if pagina:
            cuerpo["pageToken"] = pagina
        r = await peticion(ctx_http, "google_ads", "POST", f"{base(a)}/customers/{cliente}/googleAds:search",
                           json=cuerpo, headers=cabeceras(a, token, login_customer_id))
        res = json_o_error(r, "google_ads")
        filas.extend(res.get("results", []))
        pagina = res.get("nextPageToken")
        if not pagina:
            return filas


class LoginGoogle(Login):
    id = "google"
    nombre = "Google Ads"
    plataformas = ("google_ads",)

    def configurado(self, a):
        return bool(a.google_client_id and a.google_client_secret and a.google_developer_token)

    def url_autorizacion(self, a, state, redirect_uri, extra):
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": a.google_client_id, "redirect_uri": redirect_uri, "response_type": "code",
            "scope": "https://www.googleapis.com/auth/adwords", "access_type": "offline", "prompt": "consent",
            "state": state})

    async def canjear_codigo(self, http, a, params, redirect_uri, extra):
        if params.get("error"):
            raise ErrorUsuario(f"Google denegó el acceso: {params['error']}")
        exigir(params, "code")
        r = await peticion(http, "google_ads", "POST", TOKEN_URL, intentos=1, data={
            "code": params["code"], "client_id": a.google_client_id, "client_secret": a.google_client_secret,
            "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
        t = json_o_error(r, "google_ads")
        if not t.get("refresh_token"):
            raise ErrorUsuario("Google no devolvió refresh_token. Quita el acceso de la app en tu cuenta de Google "
                               "y vuelve a conectar.")
        return {"access_token": t["access_token"], "refresh_token": t["refresh_token"],
                "caduca": caduca_en(t.get("expires_in"))}

    async def refrescar(self, http, a, datos):
        r = await peticion(http, "google_ads", "POST", TOKEN_URL, data={
            "refresh_token": datos["refresh_token"], "client_id": a.google_client_id,
            "client_secret": a.google_client_secret, "grant_type": "refresh_token"})
        t = json_o_error(r, "google_ads")
        return {**datos, "access_token": t["access_token"], "caduca": caduca_en(t.get("expires_in"))}

    async def listar_cuentas(self, http, a, datos):
        token = datos["access_token"]
        cuentas: dict[str, dict] = {}
        mcc = a.google_login_customer_id.replace("-", "")
        if mcc:
            for f in await gaql(http, a, token, mcc, (
                    "SELECT customer_client.id, customer_client.descriptive_name, customer_client.currency_code, "
                    "customer_client.manager, customer_client.status FROM customer_client "
                    "WHERE customer_client.status = 'ENABLED'"), mcc):
                c = f["customerClient"]
                if not c.get("manager"):
                    cuentas[str(c["id"])] = {"plataforma": "google_ads", "cuenta_id": str(c["id"]),
                                             "nombre": c.get("descriptiveName"),
                                             "extra": {"moneda": c.get("currencyCode"), "login_customer_id": mcc}}
        else:
            r = await peticion(http, "google_ads", "GET", f"{base(a)}/customers:listAccessibleCustomers",
                               headers=cabeceras(a, token))
            for rn in json_o_error(r, "google_ads").get("resourceNames", []):
                cid = rn.split("/")[-1]
                try:
                    f = await gaql(http, a, token, cid, "SELECT customer.id, customer.descriptive_name, "
                                   "customer.currency_code, customer.manager FROM customer", cid)
                except ErrorPlataforma:
                    continue
                c = f[0]["customer"] if f else {}
                if not c.get("manager"):
                    cuentas[cid] = {"plataforma": "google_ads", "cuenta_id": cid, "nombre": c.get("descriptiveName"),
                                    "extra": {"moneda": c.get("currencyCode"), "login_customer_id": cid}}
        return list(cuentas.values())


class GoogleAds(Plataforma):
    id = "google_ads"
    nombre = "Google Ads"
    tipo = "ads"
    login = "google"

    def _q(self, ctx: Contexto, consulta: str):
        return gaql(ctx.http, ctx.ajustes, ctx.datos["access_token"], ctx.cuenta_id, consulta,
                    ctx.extra.get("login_customer_id"))

    async def metricas(self, ctx, desde: date, hasta: date, opciones):
        res = await self._q(ctx, (
            "SELECT segments.date, customer.currency_code, campaign.id, campaign.name, metrics.cost_micros, "
            "metrics.impressions, metrics.clicks, metrics.conversions, metrics.conversions_value FROM campaign "
            f"WHERE segments.date BETWEEN '{desde.isoformat()}' AND '{hasta.isoformat()}'"))
        filas = []
        for f in res:
            m, c = f.get("metrics", {}), f.get("campaign", {})
            filas.append(fila_ads(f["segments"]["date"], "google_ads", ctx.cuenta_id, c.get("id"), c.get("name"),
                                  num(m.get("costMicros")) / 1_000_000, m.get("impressions"), m.get("clicks"),
                                  m.get("conversions", 0), m.get("conversionsValue", 0),
                                  f.get("customer", {}).get("currencyCode")))
        return {"filas": filas, "notas": ["Conversiones = columna 'Conversiones' de Google Ads (acciones primarias)."]}

    async def contenido(self, ctx, opciones):
        res = await self._q(ctx, (
            "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, "
            "campaign_budget.amount_micros, campaign_budget.explicitly_shared FROM campaign "
            "WHERE campaign.status != 'REMOVED' ORDER BY campaign.name"))
        elementos = [{"id": f["campaign"]["id"], "nombre": f["campaign"].get("name"),
                      "estado": f["campaign"].get("status"), "tipo": f["campaign"].get("advertisingChannelType"),
                      "presupuesto_diario": num(f.get("campaignBudget", {}).get("amountMicros")) / 1_000_000,
                      "presupuesto_compartido": f.get("campaignBudget", {}).get("explicitlyShared", False)}
                     for f in res]
        return {"tipo": "campanas", "moneda": ctx.extra.get("moneda"), "elementos": elementos}

    def acciones(self):
        return {
            "cambiar_estado": Accion("cambiar_estado", "gestionar", "Pausa o activa una campaña.",
                                     {"campana_id": "ID de la campaña", "estado": "pausar | activar"},
                                     self._prev_estado, self._ejec_estado),
            "cambiar_presupuesto_diario": Accion(
                "cambiar_presupuesto_diario", "gestionar", "Cambia el presupuesto diario de una campaña.",
                {"campana_id": "ID de la campaña", "importe": "nuevo presupuesto diario en la moneda de la cuenta"},
                self._prev_presupuesto, self._ejec_presupuesto),
        }

    async def _campana(self, ctx, campana_id):
        cid = solo_digitos(campana_id, "campana_id")
        res = await self._q(ctx, (
            "SELECT campaign.id, campaign.name, campaign.status, campaign_budget.resource_name, "
            "campaign_budget.amount_micros, campaign_budget.explicitly_shared, customer.currency_code "
            f"FROM campaign WHERE campaign.id = {cid}"))
        if not res:
            raise ErrorUsuario("No existe esa campaña en la cuenta de esta conexión")
        return cid, res[0]

    async def _prev_estado(self, ctx, p):
        exigir(p, "campana_id", "estado")
        nuevo = "PAUSED" if opcion(p["estado"], ESTADOS_ON_OFF, "estado") == "off" else "ENABLED"
        cid, f = await self._campana(ctx, p["campana_id"])
        avisos = ["Activar empieza a gastar presupuesto."] if nuevo == "ENABLED" else []
        return {"resumen": f"Google Ads · '{f['campaign'].get('name')}': {f['campaign'].get('status')} → {nuevo}",
                "estado_actual": f["campaign"].get("status"), "estado_nuevo": nuevo, "avisos": avisos,
                "_params": {"campana_id": cid, "estado": nuevo}}

    async def _ejec_estado(self, ctx, p):
        a = ctx.ajustes
        r = await peticion(ctx.http, "google_ads", "POST", f"{base(a)}/customers/{ctx.cuenta_id}/campaigns:mutate",
                           intentos=1, headers=cabeceras(a, ctx.datos["access_token"], ctx.extra.get("login_customer_id")),
                           json={"operations": [{"update": {
                               "resourceName": f"customers/{ctx.cuenta_id}/campaigns/{p['campana_id']}",
                               "status": p["estado"]}, "updateMask": "status"}]})
        return {"ok": True, "respuesta": json_o_error(r, "google_ads")}

    async def _prev_presupuesto(self, ctx, p):
        exigir(p, "campana_id", "importe")
        importe = importe_positivo(p["importe"])
        cid, f = await self._campana(ctx, p["campana_id"])
        presu = f.get("campaignBudget", {})
        moneda = f.get("customer", {}).get("currencyCode")
        actual = num(presu.get("amountMicros")) / 1_000_000
        avisos = []
        if presu.get("explicitlyShared"):
            avisos.append("Este presupuesto es COMPARTIDO: el cambio afecta a todas las campañas que lo usan.")
        return {"resumen": f"Google Ads · '{f['campaign'].get('name')}': presupuesto diario {actual} → {importe} {moneda}",
                "cambio_presupuesto": {"actual": actual, "nuevo": importe, "moneda": moneda}, "avisos": avisos,
                "_params": {"presupuesto": presu["resourceName"], "micros": str(int(round(importe * 1_000_000)))}}

    async def _ejec_presupuesto(self, ctx, p):
        a = ctx.ajustes
        r = await peticion(ctx.http, "google_ads", "POST",
                           f"{base(a)}/customers/{ctx.cuenta_id}/campaignBudgets:mutate", intentos=1,
                           headers=cabeceras(a, ctx.datos["access_token"], ctx.extra.get("login_customer_id")),
                           json={"operations": [{"update": {"resourceName": p["presupuesto"],
                                                            "amountMicros": p["micros"]},
                                                 "updateMask": "amountMicros"}]})
        return {"ok": True, "respuesta": json_o_error(r, "google_ads")}
