"""TikTok: API for Business (TikTok Ads) y TikTok for Developers (cuenta orgánica)."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from urllib.parse import urlencode

from ..config import Settings
from ..http import ErrorPlataforma, ErrorUsuario, json_o_error, peticion
from .base import (ESTADOS_ON_OFF, Accion, Contexto, Login, Plataforma, caduca_en, exigir, fila_ads,
                   importe_positivo, num, opcion, solo_digitos, tramos)


def base_ads(a: Settings) -> str:
    return f"https://business-api.tiktok.com/open_api/{a.tiktok_ads_version}"


def _ok(cuerpo: dict, plataforma: str = "tiktok_ads") -> dict:
    """La API for Business responde 200 con code != 0 cuando hay error."""
    if cuerpo.get("code") not in (0, None):
        raise ErrorPlataforma(plataforma, cuerpo.get("message") or f"code {cuerpo.get('code')}",
                              detalle={"code": cuerpo.get("code"), "request_id": cuerpo.get("request_id")})
    return cuerpo.get("data") or {}


async def ads_get(ctx: Contexto, ruta: str, params: dict) -> dict:
    p = {k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in params.items()}
    r = await peticion(ctx.http, "tiktok_ads", "GET", f"{base_ads(ctx.ajustes)}/{ruta}", params=p,
                       headers={"Access-Token": ctx.datos["access_token"]})
    return _ok(json_o_error(r, "tiktok_ads"))


async def ads_post(ctx: Contexto, ruta: str, cuerpo: dict) -> dict:
    r = await peticion(ctx.http, "tiktok_ads", "POST", f"{base_ads(ctx.ajustes)}/{ruta}", intentos=1, json=cuerpo,
                       headers={"Access-Token": ctx.datos["access_token"]})
    return _ok(json_o_error(r, "tiktok_ads"))


class LoginTikTokAds(Login):
    id = "tiktok_ads"
    nombre = "TikTok Ads"
    plataformas = ("tiktok_ads",)

    def configurado(self, a):
        return bool(a.tiktok_ads_app_id and a.tiktok_ads_secret)

    def url_autorizacion(self, a, state, redirect_uri, extra):
        return "https://business-api.tiktok.com/portal/auth?" + urlencode(
            {"app_id": a.tiktok_ads_app_id, "state": state, "redirect_uri": redirect_uri})

    async def canjear_codigo(self, http, a, params, redirect_uri, extra):
        codigo = params.get("auth_code") or params.get("code")
        if not codigo:
            raise ErrorUsuario("TikTok no devolvió auth_code")
        r = await peticion(http, "tiktok_ads", "POST", f"{base_ads(a)}/oauth2/access_token/", intentos=1,
                           json={"app_id": a.tiktok_ads_app_id, "secret": a.tiktok_ads_secret, "auth_code": codigo})
        d = _ok(json_o_error(r, "tiktok_ads"))
        return {"access_token": d["access_token"], "advertiser_ids": d.get("advertiser_ids", [])}

    async def listar_cuentas(self, http, a, datos):
        ctx = Contexto(http, a, datos)
        d = await ads_get(ctx, "oauth2/advertiser/get/", {"app_id": a.tiktok_ads_app_id, "secret": a.tiktok_ads_secret})
        lista = d.get("list", [])
        monedas = {}
        if lista:
            try:
                info = await ads_get(ctx, "advertiser/info/", {
                    "advertiser_ids": [str(x["advertiser_id"]) for x in lista], "fields": ["advertiser_id", "currency"]})
                monedas = {str(x.get("advertiser_id")): x.get("currency") for x in info.get("list", [])}
            except ErrorPlataforma:
                pass
        return [{"plataforma": "tiktok_ads", "cuenta_id": str(x["advertiser_id"]), "nombre": x.get("advertiser_name"),
                 "extra": {"moneda": monedas.get(str(x["advertiser_id"]))}} for x in lista]


class TikTokAds(Plataforma):
    id = "tiktok_ads"
    nombre = "TikTok Ads"
    tipo = "ads"
    login = "tiktok_ads"

    async def metricas(self, ctx, desde, hasta, opciones):
        valor = ctx.ajustes.tiktok_metrica_valor
        metricas = ["campaign_name", "spend", "impressions", "clicks", "conversion"] + ([valor] if valor else [])
        filas = []
        for ini, fin in tramos(desde, hasta, 30):  # la API limita los informes diarios a 30 días por petición
            pagina = 1
            while True:
                d = await ads_get(ctx, "report/integrated/get/", {
                    "advertiser_id": ctx.cuenta_id, "report_type": "BASIC", "data_level": "AUCTION_CAMPAIGN",
                    "dimensions": ["campaign_id", "stat_time_day"], "metrics": metricas,
                    "start_date": ini.isoformat(), "end_date": fin.isoformat(), "page": pagina, "page_size": 1000})
                for f in d.get("list", []):
                    dim, m = f.get("dimensions", {}), f.get("metrics", {})
                    filas.append(fila_ads(str(dim.get("stat_time_day", ""))[:10], "tiktok_ads", ctx.cuenta_id,
                                          dim.get("campaign_id"), m.get("campaign_name"), m.get("spend"),
                                          m.get("impressions"), m.get("clicks"), m.get("conversion"),
                                          m.get(valor) if valor else None, ctx.extra.get("moneda")))
                info = d.get("page_info") or {}
                if pagina >= int(info.get("total_page") or 1):
                    break
                pagina += 1
        notas = ["Conversiones = evento de optimización de cada campaña."]
        if not valor:
            notas.append("Ingresos sin dato: define TIKTOK_METRICA_VALOR si quieres traer el valor de compra.")
        return {"filas": filas, "notas": notas}

    async def contenido(self, ctx, opciones):
        d = await ads_get(ctx, "campaign/get/", {"advertiser_id": ctx.cuenta_id, "page_size": 1000})
        elementos = [{"id": c.get("campaign_id"), "nombre": c.get("campaign_name"),
                      "estado": c.get("operation_status"), "presupuesto": c.get("budget"),
                      "modo_presupuesto": c.get("budget_mode"), "objetivo": c.get("objective_type")}
                     for c in d.get("list", [])]
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
        d = await ads_get(ctx, "campaign/get/", {"advertiser_id": ctx.cuenta_id, "filtering": {"campaign_ids": [cid]}})
        if not d.get("list"):
            raise ErrorUsuario("No existe esa campaña en la cuenta de esta conexión")
        return cid, d["list"][0]

    async def _prev_estado(self, ctx, p):
        exigir(p, "campana_id", "estado")
        nuevo = "DISABLE" if opcion(p["estado"], ESTADOS_ON_OFF, "estado") == "off" else "ENABLE"
        cid, c = await self._campana(ctx, p["campana_id"])
        avisos = ["Activar empieza a gastar presupuesto."] if nuevo == "ENABLE" else []
        return {"resumen": f"TikTok Ads · '{c.get('campaign_name')}': {c.get('operation_status')} → {nuevo}",
                "estado_actual": c.get("operation_status"), "estado_nuevo": nuevo, "avisos": avisos,
                "_params": {"campana_id": cid, "estado": nuevo}}

    async def _ejec_estado(self, ctx, p):
        d = await ads_post(ctx, "campaign/status/update/", {"advertiser_id": ctx.cuenta_id,
                                                            "campaign_ids": [p["campana_id"]],
                                                            "operation_status": p["estado"]})
        return {"ok": True, "respuesta": d}

    async def _prev_presupuesto(self, ctx, p):
        exigir(p, "campana_id", "importe")
        importe = importe_positivo(p["importe"])
        cid, c = await self._campana(ctx, p["campana_id"])
        if c.get("budget_mode") != "BUDGET_MODE_DAY":
            raise ErrorUsuario(f"La campaña no tiene presupuesto diario (modo {c.get('budget_mode')}); "
                               "el presupuesto puede estar en el grupo de anuncios.")
        moneda = ctx.extra.get("moneda")
        actual = num(c.get("budget"))
        return {"resumen": f"TikTok Ads · '{c.get('campaign_name')}': presupuesto diario {actual} → {importe} {moneda or ''}",
                "cambio_presupuesto": {"actual": actual, "nuevo": importe, "moneda": moneda},
                "_params": {"campana_id": cid, "importe": importe}}

    async def _ejec_presupuesto(self, ctx, p):
        d = await ads_post(ctx, "campaign/update/", {"advertiser_id": ctx.cuenta_id, "campaign_id": p["campana_id"],
                                                     "budget": p["importe"]})
        return {"ok": True, "respuesta": d}


# ---------------------------------------------------------------- TikTok orgánico

OPEN = "https://open.tiktokapis.com/v2"
SCOPES_TT = "user.info.basic,user.info.profile,user.info.stats,video.list,video.publish"


def _ok_open(cuerpo: dict) -> dict:
    err = cuerpo.get("error") or {}
    if err.get("code") not in (None, "ok"):
        raise ErrorPlataforma("tiktok", err.get("message") or err.get("code"), detalle=err)
    return cuerpo.get("data") or {}


async def open_req(ctx: Contexto, metodo: str, ruta: str, **kw) -> dict:
    r = await peticion(ctx.http, "tiktok", metodo, f"{OPEN}/{ruta}", intentos=1 if metodo == "POST" else 3,
                       headers={"Authorization": f"Bearer {ctx.datos['access_token']}"}, **kw)
    return _ok_open(json_o_error(r, "tiktok"))


class LoginTikTokOrganico(Login):
    id = "tiktok_organico"
    nombre = "TikTok (cuenta)"
    plataformas = ("tiktok",)

    def configurado(self, a):
        return bool(a.tiktok_client_key and a.tiktok_client_secret)

    def url_autorizacion(self, a, state, redirect_uri, extra):
        return "https://www.tiktok.com/v2/auth/authorize/?" + urlencode({
            "client_key": a.tiktok_client_key, "response_type": "code", "scope": SCOPES_TT,
            "redirect_uri": redirect_uri, "state": state})

    async def _token(self, http, a, datos: dict) -> dict:
        r = await peticion(http, "tiktok", "POST", f"{OPEN}/oauth/token/", intentos=1, data={
            "client_key": a.tiktok_client_key, "client_secret": a.tiktok_client_secret, **datos},
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        t = json_o_error(r, "tiktok")
        if t.get("error") and not t.get("access_token"):
            raise ErrorPlataforma("tiktok", t.get("error_description") or t["error"])
        return {"access_token": t["access_token"], "refresh_token": t.get("refresh_token"),
                "open_id": t.get("open_id"), "caduca": caduca_en(t.get("expires_in"))}

    async def canjear_codigo(self, http, a, params, redirect_uri, extra):
        if params.get("error"):
            raise ErrorUsuario(f"TikTok denegó el acceso: {params.get('error_description') or params['error']}")
        exigir(params, "code")
        return await self._token(http, a, {"code": params["code"], "grant_type": "authorization_code",
                                           "redirect_uri": redirect_uri})

    async def refrescar(self, http, a, datos):
        nuevo = await self._token(http, a, {"grant_type": "refresh_token", "refresh_token": datos["refresh_token"]})
        return {**datos, **{k: v for k, v in nuevo.items() if v}}

    async def listar_cuentas(self, http, a, datos):
        ctx = Contexto(http, a, datos)
        d = await open_req(ctx, "GET", "user/info/", params={"fields": "open_id,display_name,username"})
        u = d.get("user", {})
        return [{"plataforma": "tiktok", "cuenta_id": u.get("open_id") or datos.get("open_id"),
                 "nombre": "@" + (u.get("username") or u.get("display_name") or ""), "extra": {}}]


CAMPOS_VIDEO = "id,title,video_description,create_time,share_url,view_count,like_count,comment_count,share_count"


class TikTok(Plataforma):
    id = "tiktok"
    nombre = "TikTok"
    tipo = "social"
    login = "tiktok_organico"

    async def _videos(self, ctx, max_videos: int = 200) -> list[dict]:
        videos, cursor = [], None
        while len(videos) < max_videos:
            cuerpo = {"max_count": 20, **({"cursor": cursor} if cursor else {})}
            d = await open_req(ctx, "POST", "video/list/", params={"fields": CAMPOS_VIDEO}, json=cuerpo)
            videos.extend(d.get("videos", []))
            if not d.get("has_more"):
                break
            cursor = d.get("cursor")
        return videos

    async def metricas(self, ctx, desde, hasta, opciones):
        hoy = datetime.now(timezone.utc).date().isoformat()
        u = (await open_req(ctx, "GET", "user/info/", params={
            "fields": "follower_count,following_count,likes_count,video_count"})).get("user", {})
        filas = [{"fecha": hoy, "plataforma": "tiktok", "cuenta_id": ctx.cuenta_id, "metrica": k, "valor": v,
                  "periodo": "instantanea"} for k, v in u.items()]
        for v in await self._videos(ctx):
            dia = datetime.fromtimestamp(int(v.get("create_time", 0)), timezone.utc).date()
            if desde <= dia <= hasta:
                for k in ("view_count", "like_count", "comment_count", "share_count"):
                    filas.append({"fecha": dia.isoformat(), "plataforma": "tiktok", "cuenta_id": ctx.cuenta_id,
                                  "metrica": k, "valor": v.get(k), "video_id": v.get("id"),
                                  "periodo": "acumulado_desde_publicacion"})
        return {"filas": filas, "notas": [
            "La API de TikTok no da series diarias de la cuenta: seguidores y totales son una foto de hoy.",
            "Las métricas por vídeo son acumuladas desde su publicación, para vídeos publicados en el rango."]}

    async def contenido(self, ctx, opciones):
        return {"tipo": "videos", "elementos": await self._videos(ctx, int(opciones.get("limite", 40)))}

    def acciones(self):
        return {"publicar_video": Accion(
            "publicar_video", "publicar",
            "Publica un vídeo desde una URL de un dominio verificado en la app de TikTok.",
            {"video_url": "URL https del vídeo (dominio verificado en TikTok for Developers)",
             "texto": "descripción con hashtags",
             "privacidad": "publico | amigos | seguidores | privado (por defecto, el más abierto permitido)"},
            self._prev, self._ejec)}

    async def _prev(self, ctx, p):
        exigir(p, "video_url")
        if not str(p["video_url"]).startswith("https://"):
            raise ErrorUsuario("video_url debe empezar por https://")
        info = await open_req(ctx, "POST", "post/publish/creator_info/query/", json={})
        opciones = info.get("privacy_level_options") or []
        mapa = {"publico": "PUBLIC_TO_EVERYONE", "amigos": "MUTUAL_FOLLOW_FRIENDS",
                "seguidores": "FOLLOWER_OF_CREATOR", "privado": "SELF_ONLY"}
        if p.get("privacidad"):
            nivel = opcion(p["privacidad"], {**mapa, **{v.lower(): v for v in mapa.values()}}, "privacidad")
        else:
            nivel = next((n for n in ("PUBLIC_TO_EVERYONE", "FOLLOWER_OF_CREATOR", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY")
                          if n in opciones), "SELF_ONLY")
        if opciones and nivel not in opciones:
            raise ErrorUsuario(f"Privacidad {nivel} no permitida para esta cuenta. Opciones: {', '.join(opciones)}")
        avisos = ["Si la app de TikTok no ha pasado la auditoría, TikTok fuerza la publicación como privada."]
        return {"resumen": f"TikTok · @{info.get('creator_username') or ctx.cuenta_id}: publicar vídeo ({nivel})",
                "contenido": {"video_url": p["video_url"], "texto": p.get("texto", ""), "privacidad": nivel},
                "avisos": avisos,
                "_params": {"video_url": p["video_url"], "texto": p.get("texto", ""), "privacidad": nivel}}

    async def _ejec(self, ctx, p):
        d = await open_req(ctx, "POST", "post/publish/video/init/", json={
            "post_info": {"title": p["texto"][:2200], "privacy_level": p["privacidad"]},
            "source_info": {"source": "PULL_FROM_URL", "video_url": p["video_url"]}})
        publish_id = d.get("publish_id")
        estado = None
        for _ in range(12):  # hasta ~1 minuto
            await asyncio.sleep(5)
            s = await open_req(ctx, "POST", "post/publish/status/fetch/", json={"publish_id": publish_id})
            estado = s.get("status")
            if estado in ("PUBLISH_COMPLETE", "FAILED"):
                if estado == "FAILED":
                    raise ErrorPlataforma("tiktok", f"TikTok no publicó el vídeo: {s.get('fail_reason')}")
                break
        return {"ok": True, "publish_id": publish_id, "estado": estado or "EN_PROCESO"}
