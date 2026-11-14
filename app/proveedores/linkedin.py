"""LinkedIn: Marketing API (Ads) y Community Management API (páginas de empresa).

LinkedIn exige apps separadas para Ads y para Community Management, por eso hay dos logins.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import quote, urlencode

from ..config import Settings
from ..http import ErrorPlataforma, ErrorUsuario, json_o_error, peticion
from .base import (ESTADOS_ON_OFF, Accion, Contexto, Login, Plataforma, caduca_en, exigir, fila_ads,
                   importe_positivo, num, opcion, solo_digitos, tramos)

API = "https://api.linkedin.com/rest"
AUTH = "https://www.linkedin.com/oauth/v2"


def urn(tipo: str, id_: str) -> str:
    return f"urn:li:{tipo}:{id_}"


def enc(u: str) -> str:
    """Codifica un URN para meterlo dentro de una expresión Rest.li."""
    return quote(u, safe="")


def cab(a: Settings, token: str, **extra) -> dict:
    return {"Authorization": f"Bearer {token}", "Linkedin-Version": a.linkedin_version,
            "X-Restli-Protocol-Version": "2.0.0", **extra}


async def li_get(ctx: Contexto, ruta_y_query: str, plataforma: str, **cabeceras) -> dict:
    """GET con la query ya construida (Rest.li no admite la codificación estándar de paréntesis)."""
    r = await peticion(ctx.http, plataforma, "GET", f"{API}/{ruta_y_query}",
                       headers=cab(ctx.ajustes, ctx.datos["access_token"], **cabeceras))
    return json_o_error(r, plataforma)


def rango_restli(ini: date, fin: date) -> str:
    return (f"(start:(year:{ini.year},month:{ini.month},day:{ini.day}),"
            f"end:(year:{fin.year},month:{fin.month},day:{fin.day}))")


class _LoginLinkedIn(Login):
    scopes: tuple[str, ...] = ()
    _cid: str = ""
    _sec: str = ""

    def _creds(self, a):
        return getattr(a, self._cid), getattr(a, self._sec)

    def configurado(self, a):
        return all(self._creds(a))

    def url_autorizacion(self, a, state, redirect_uri, extra):
        return f"{AUTH}/authorization?" + urlencode({
            "response_type": "code", "client_id": self._creds(a)[0], "redirect_uri": redirect_uri,
            "state": state, "scope": " ".join(self.scopes)})

    async def canjear_codigo(self, http, a, params, redirect_uri, extra):
        if params.get("error"):
            raise ErrorUsuario(f"LinkedIn denegó el acceso: {params.get('error_description') or params['error']}")
        exigir(params, "code")
        cid, sec = self._creds(a)
        r = await peticion(http, self.id, "POST", f"{AUTH}/accessToken", intentos=1, data={
            "grant_type": "authorization_code", "code": params["code"], "redirect_uri": redirect_uri,
            "client_id": cid, "client_secret": sec})
        t = json_o_error(r, self.id)
        return {"access_token": t["access_token"], "refresh_token": t.get("refresh_token"),
                "caduca": caduca_en(t.get("expires_in")),
                "refresh_caduca": caduca_en(t.get("refresh_token_expires_in"))}

    def necesita_refresco(self, datos):
        return bool(datos.get("refresh_token")) and super().necesita_refresco(datos)

    async def refrescar(self, http, a, datos):
        cid, sec = self._creds(a)
        r = await peticion(http, self.id, "POST", f"{AUTH}/accessToken", data={
            "grant_type": "refresh_token", "refresh_token": datos["refresh_token"],
            "client_id": cid, "client_secret": sec})
        t = json_o_error(r, self.id)
        return {**datos, "access_token": t["access_token"],
                "refresh_token": t.get("refresh_token", datos["refresh_token"]),
                "caduca": caduca_en(t.get("expires_in"))}


class LoginLinkedInAds(_LoginLinkedIn):
    id = "linkedin_ads"
    nombre = "LinkedIn Ads"
    plataformas = ("linkedin_ads",)
    scopes = ("r_ads", "r_ads_reporting", "rw_ads")
    _cid, _sec = "linkedin_ads_client_id", "linkedin_ads_client_secret"

    async def listar_cuentas(self, http, a, datos):
        ctx = Contexto(http, a, datos)
        res = await li_get(ctx, "adAccountUsers?q=authenticatedUser", "linkedin_ads")
        cuentas = []
        for e in res.get("elements", []):
            acc_id = str(e.get("account", "")).split(":")[-1]
            if not acc_id:
                continue
            try:
                info = await li_get(ctx, f"adAccounts/{acc_id}", "linkedin_ads")
            except ErrorPlataforma:
                info = {}
            cuentas.append({"plataforma": "linkedin_ads", "cuenta_id": acc_id, "nombre": info.get("name"),
                            "extra": {"moneda": info.get("currency"), "rol": e.get("role")}})
        return cuentas


class LoginLinkedInOrganico(_LoginLinkedIn):
    id = "linkedin_organico"
    nombre = "LinkedIn (páginas de empresa)"
    plataformas = ("linkedin",)
    scopes = ("r_organization_social", "w_organization_social", "rw_organization_admin")
    _cid, _sec = "linkedin_cm_client_id", "linkedin_cm_client_secret"

    async def listar_cuentas(self, http, a, datos):
        ctx = Contexto(http, a, datos)
        res = await li_get(ctx, "organizationAcls?q=roleAssignee&role=ADMINISTRATOR&state=APPROVED", "linkedin")
        cuentas = []
        for e in res.get("elements", []):
            org_id = str(e.get("organization", "")).split(":")[-1]
            if not org_id:
                continue
            try:
                info = await li_get(ctx, f"organizations/{org_id}", "linkedin")
            except ErrorPlataforma:
                info = {}
            cuentas.append({"plataforma": "linkedin", "cuenta_id": org_id,
                            "nombre": info.get("localizedName"), "extra": {}})
        return cuentas


# ---------------------------------------------------------------- LinkedIn Ads

class LinkedInAds(Plataforma):
    id = "linkedin_ads"
    nombre = "LinkedIn Ads"
    tipo = "ads"
    login = "linkedin_ads"

    async def _campanas(self, ctx) -> list[dict]:
        salida, token_pag = [], None
        for _ in range(50):
            q = "q=search&pageSize=100" + (f"&pageToken={quote(token_pag)}" if token_pag else "")
            res = await li_get(ctx, f"adAccounts/{ctx.cuenta_id}/adCampaigns?{q}", "linkedin_ads")
            salida.extend(res.get("elements", []))
            token_pag = (res.get("metadata") or {}).get("nextPageToken")
            if not token_pag:
                break
        return salida

    async def metricas(self, ctx, desde, hasta, opciones):
        nombres = {str(c.get("id")): c.get("name") for c in await self._campanas(ctx)}
        cuenta = enc(urn("sponsoredAccount", ctx.cuenta_id))
        campos = ("dateRange,pivotValues,costInLocalCurrency,impressions,clicks,externalWebsiteConversions,"
                  "conversionValueInLocalCurrency")
        filas = []
        for ini, fin in tramos(desde, hasta, 180):
            res = await li_get(ctx, (
                f"adAnalytics?q=analytics&pivot=CAMPAIGN&timeGranularity=DAILY&dateRange={rango_restli(ini, fin)}"
                f"&accounts=List({cuenta})&fields={campos}"), "linkedin_ads")
            for e in res.get("elements", []):
                inicio = e["dateRange"]["start"]
                fecha = date(inicio["year"], inicio["month"], inicio["day"]).isoformat()
                camp = str((e.get("pivotValues") or [""])[0]).split(":")[-1]
                filas.append(fila_ads(fecha, "linkedin_ads", ctx.cuenta_id, camp, nombres.get(camp),
                                      e.get("costInLocalCurrency"), e.get("impressions"), e.get("clicks"),
                                      e.get("externalWebsiteConversions", 0),
                                      e.get("conversionValueInLocalCurrency", 0), ctx.extra.get("moneda")))
        return {"filas": filas, "notas": ["Conversiones = conversiones web (Insight Tag / API de conversiones)."]}

    async def contenido(self, ctx, opciones):
        elementos = [{"id": c.get("id"), "nombre": c.get("name"), "estado": c.get("status"),
                      "presupuesto_diario": num((c.get("dailyBudget") or {}).get("amount")) or None,
                      "objetivo": c.get("objectiveType")} for c in await self._campanas(ctx)]
        return {"tipo": "campanas", "moneda": ctx.extra.get("moneda"), "elementos": elementos}

    def acciones(self):
        return {
            "cambiar_estado": Accion("cambiar_estado", "gestionar", "Pausa o activa una campaña.",
                                     {"campana_id": "ID de la campaña", "estado": "pausar | activar"},
                                     self._prev_estado, self._ejec),
            "cambiar_presupuesto_diario": Accion(
                "cambiar_presupuesto_diario", "gestionar", "Cambia el presupuesto diario de una campaña.",
                {"campana_id": "ID de la campaña", "importe": "nuevo presupuesto diario en la moneda de la cuenta"},
                self._prev_presupuesto, self._ejec),
        }

    async def _campana(self, ctx, campana_id):
        cid = solo_digitos(campana_id, "campana_id")
        try:
            return cid, await li_get(ctx, f"adAccounts/{ctx.cuenta_id}/adCampaigns/{cid}", "linkedin_ads")
        except ErrorPlataforma as e:
            if e.estado == 404:
                raise ErrorUsuario("No existe esa campaña en la cuenta de esta conexión") from e
            raise

    async def _prev_estado(self, ctx, p):
        exigir(p, "campana_id", "estado")
        nuevo = "PAUSED" if opcion(p["estado"], ESTADOS_ON_OFF, "estado") == "off" else "ACTIVE"
        cid, c = await self._campana(ctx, p["campana_id"])
        avisos = ["Activar empieza a gastar presupuesto."] if nuevo == "ACTIVE" else []
        return {"resumen": f"LinkedIn Ads · '{c.get('name')}': {c.get('status')} → {nuevo}",
                "estado_actual": c.get("status"), "estado_nuevo": nuevo, "avisos": avisos,
                "_params": {"campana_id": cid, "set": {"status": nuevo}}}

    async def _prev_presupuesto(self, ctx, p):
        exigir(p, "campana_id", "importe")
        importe = importe_positivo(p["importe"])
        cid, c = await self._campana(ctx, p["campana_id"])
        presu = c.get("dailyBudget") or {}
        if not presu:
            raise ErrorUsuario("Esta campaña no usa presupuesto diario (puede tener presupuesto total).")
        moneda = presu.get("currencyCode") or ctx.extra.get("moneda")
        actual = num(presu.get("amount"))
        return {"resumen": f"LinkedIn Ads · '{c.get('name')}': presupuesto diario {actual} → {importe} {moneda}",
                "cambio_presupuesto": {"actual": actual, "nuevo": importe, "moneda": moneda},
                "_params": {"campana_id": cid,
                            "set": {"dailyBudget": {"amount": f"{importe:.2f}", "currencyCode": moneda}}}}

    async def _ejec(self, ctx, p):
        r = await peticion(ctx.http, "linkedin_ads", "POST",
                           f"{API}/adAccounts/{ctx.cuenta_id}/adCampaigns/{p['campana_id']}", intentos=1,
                           headers=cab(ctx.ajustes, ctx.datos["access_token"], **{"X-RestLi-Method": "PARTIAL_UPDATE"}),
                           json={"patch": {"$set": p["set"]}})
        json_o_error(r, "linkedin_ads")
        return {"ok": True, "estado_http": r.status_code}


# ---------------------------------------------------------------- LinkedIn orgánico

class LinkedIn(Plataforma):
    id = "linkedin"
    nombre = "LinkedIn (página de empresa)"
    tipo = "social"
    login = "linkedin_organico"

    async def metricas(self, ctx, desde, hasta, opciones):
        org = enc(urn("organization", ctx.cuenta_id))
        filas = []
        for ini, fin in tramos(desde, hasta, 365):
            inicio_ms = int(datetime.combine(ini, time(), timezone.utc).timestamp() * 1000)
            fin_ms = int(datetime.combine(fin + timedelta(days=1), time(), timezone.utc).timestamp() * 1000)
            intervalo = f"(timeRange:(start:{inicio_ms},end:{fin_ms}),timeGranularityType:DAY)"
            comp = await li_get(ctx, (f"organizationalEntityShareStatistics?q=organizationalEntity"
                                      f"&organizationalEntity={org}&timeIntervals={intervalo}"), "linkedin")
            for e in comp.get("elements", []):
                fecha = datetime.fromtimestamp(e["timeRange"]["start"] / 1000, timezone.utc).date().isoformat()
                for k, v in (e.get("totalShareStatistics") or {}).items():
                    filas.append({"fecha": fecha, "plataforma": "linkedin", "cuenta_id": ctx.cuenta_id,
                                  "metrica": k, "valor": v})
            seg = await li_get(ctx, (f"organizationalEntityFollowerStatistics?q=organizationalEntity"
                                     f"&organizationalEntity={org}&timeIntervals={intervalo}"), "linkedin")
            for e in seg.get("elements", []):
                fecha = datetime.fromtimestamp(e["timeRange"]["start"] / 1000, timezone.utc).date().isoformat()
                for k, v in (e.get("followerGains") or {}).items():
                    filas.append({"fecha": fecha, "plataforma": "linkedin", "cuenta_id": ctx.cuenta_id,
                                  "metrica": f"seguidores.{k}", "valor": v})
        return {"filas": filas, "notas": ["Días en UTC. LinkedIn solo guarda estadísticas diarias de los últimos 12 meses."]}

    async def contenido(self, ctx, opciones):
        org = enc(urn("organization", ctx.cuenta_id))
        res = await li_get(ctx, f"posts?q=author&author={org}&count={int(opciones.get('limite', 20))}", "linkedin",
                           **{"X-RestLi-Method": "FINDER"})
        elementos = [{"id": e.get("id"), "texto": e.get("commentary"), "estado": e.get("lifecycleState"),
                      "publicado": e.get("publishedAt")} for e in res.get("elements", [])]
        return {"tipo": "publicaciones", "elementos": elementos}

    def acciones(self):
        return {"publicar": Accion(
            "publicar", "publicar", "Publica un post en la página de empresa, con enlace o imagen opcional.",
            {"texto": "texto del post", "enlace": "URL de un artículo (opcional)",
             "titulo_enlace": "título del enlace (opcional)", "imagen_url": "URL pública de una imagen (opcional)"},
            self._prev, self._ejec)}

    async def _prev(self, ctx, p):
        exigir(p, "texto")
        if p.get("enlace") and p.get("imagen_url"):
            raise ErrorUsuario("Usa enlace o imagen_url, no los dos.")
        if len(p["texto"]) > 3000:
            raise ErrorUsuario("LinkedIn admite como mucho 3.000 caracteres")
        org = await li_get(ctx, f"organizations/{solo_digitos(ctx.cuenta_id, 'cuenta')}", "linkedin")
        return {"resumen": f"LinkedIn · {org.get('localizedName')}: publicación inmediata",
                "contenido": {k: p.get(k) for k in ("texto", "enlace", "titulo_enlace", "imagen_url") if p.get(k)},
                "_params": {k: p.get(k) for k in ("texto", "enlace", "titulo_enlace", "imagen_url")}}

    async def _ejec(self, ctx, p):
        autor = urn("organization", ctx.cuenta_id)
        cuerpo = {"author": autor, "commentary": _escapar_commentary(p["texto"]), "visibility": "PUBLIC",
                  "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                                   "thirdPartyDistributionChannels": []},
                  "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}
        if p.get("enlace"):
            cuerpo["content"] = {"article": {"source": p["enlace"], "title": p.get("titulo_enlace") or p["enlace"]}}
        if p.get("imagen_url"):
            cuerpo["content"] = {"media": {"id": await self._subir_imagen(ctx, autor, p["imagen_url"])}}
        r = await peticion(ctx.http, "linkedin", "POST", f"{API}/posts", intentos=1,
                           headers=cab(ctx.ajustes, ctx.datos["access_token"]), json=cuerpo)
        json_o_error(r, "linkedin")
        return {"ok": True, "id": r.headers.get("x-restli-id")}

    async def _subir_imagen(self, ctx, autor, url) -> str:
        img = await peticion(ctx.http, "linkedin", "GET", url)
        if img.status_code != 200 or not img.headers.get("content-type", "").startswith("image/"):
            raise ErrorUsuario("No se pudo descargar la imagen o no es una imagen")
        r = await peticion(ctx.http, "linkedin", "POST", f"{API}/images?action=initializeUpload", intentos=1,
                           headers=cab(ctx.ajustes, ctx.datos["access_token"]),
                           json={"initializeUploadRequest": {"owner": autor}})
        val = json_o_error(r, "linkedin")["value"]
        sub = await peticion(ctx.http, "linkedin", "PUT", val["uploadUrl"], intentos=1, content=img.content,
                             headers={"Authorization": f"Bearer {ctx.datos['access_token']}"})
        if sub.status_code >= 400:
            raise ErrorPlataforma("linkedin", f"Fallo al subir la imagen (HTTP {sub.status_code})")
        return val["image"]


def _escapar_commentary(texto: str) -> str:
    """El campo commentary usa 'little text': hay que escapar estos caracteres reservados."""
    for c in "\\|{}@[]()<>#*_~":
        texto = texto.replace(c, "\\" + c)
    return texto
