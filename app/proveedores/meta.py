"""Meta: un login da acceso a Meta Ads, Páginas de Facebook e Instagram (Graph API / Marketing API)."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from ..config import Settings
from ..http import ErrorPlataforma, ErrorUsuario, json_o_error, peticion
from .base import (ESTADOS_ON_OFF, Accion, Contexto, Login, Plataforma, caduca_en, exigir, fila_ads,
                   importe_positivo, num, opcion, tramos)

SCOPES = [
    "ads_read", "ads_management", "business_management",
    "pages_show_list", "pages_read_engagement", "pages_manage_posts", "read_insights",
    "instagram_basic", "instagram_content_publish", "instagram_manage_insights",
]
# Monedas sin decimales en Meta (los presupuestos van en la unidad mínima de la moneda)
SIN_DECIMALES = {"CLP", "COP", "CRC", "HUF", "ISK", "IDR", "JPY", "KRW", "PYG", "TWD", "VND"}
PRIORIDAD_CONVERSION = ["omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase"]


def graph(a: Settings) -> str:
    return f"https://graph.facebook.com/{a.meta_version}"


async def get(ctx_http: httpx.AsyncClient, a: Settings, ruta: str, token: str, params: dict | None = None,
              plataforma: str = "meta") -> dict:
    p = dict(params or {})
    p["access_token"] = token
    url = ruta if ruta.startswith("https://") else f"{graph(a)}/{ruta.lstrip('/')}"
    r = await peticion(ctx_http, plataforma, "GET", url, params=p)
    return json_o_error(r, plataforma)


async def post(ctx_http: httpx.AsyncClient, a: Settings, ruta: str, token: str, data: dict,
               plataforma: str = "meta") -> dict:
    d = dict(data)
    d["access_token"] = token
    r = await peticion(ctx_http, plataforma, "POST", f"{graph(a)}/{ruta.lstrip('/')}", data=d, intentos=1)
    return json_o_error(r, plataforma)


async def paginar(http: httpx.AsyncClient, a: Settings, ruta: str, token: str, params: dict,
                  plataforma: str = "meta", max_paginas: int = 50) -> list[dict]:
    salida: list[dict] = []
    res = await get(http, a, ruta, token, params, plataforma)
    for _ in range(max_paginas):
        salida.extend(res.get("data", []))
        siguiente = (res.get("paging") or {}).get("next")
        if not siguiente:
            break
        r = await peticion(http, plataforma, "GET", siguiente)
        res = json_o_error(r, plataforma)
    return salida


class LoginMeta(Login):
    id = "meta"
    nombre = "Meta (Facebook, Instagram y Meta Ads)"
    plataformas = ("meta_ads", "facebook", "instagram")
    campos_manuales = {"access_token": "Token de un usuario del sistema de tu Business Manager (no caduca)"}

    def configurado(self, a: Settings) -> bool:
        return bool(a.meta_app_id and a.meta_app_secret)

    def url_autorizacion(self, a, state, redirect_uri, extra):
        q = urlencode({"client_id": a.meta_app_id, "redirect_uri": redirect_uri, "state": state,
                       "scope": ",".join(SCOPES), "response_type": "code"})
        return f"https://www.facebook.com/{a.meta_version}/dialog/oauth?{q}"

    async def canjear_codigo(self, http, a, params, redirect_uri, extra):
        if params.get("error"):
            raise ErrorUsuario(f"Meta denegó el acceso: {params.get('error_description') or params['error']}")
        exigir(params, "code")
        corto = await get(http, a, "oauth/access_token", "", {
            "client_id": a.meta_app_id, "client_secret": a.meta_app_secret,
            "redirect_uri": redirect_uri, "code": params["code"]})
        largo = await get(http, a, "oauth/access_token", "", {
            "grant_type": "fb_exchange_token", "client_id": a.meta_app_id,
            "client_secret": a.meta_app_secret, "fb_exchange_token": corto["access_token"]})
        return {"access_token": largo["access_token"], "caduca": caduca_en(largo.get("expires_in")),
                "tipo": "usuario"}

    async def preparar_manual(self, http, a, datos):
        limpio = await super().preparar_manual(http, a, datos)
        yo = await get(http, a, "me", limpio["access_token"], {"fields": "id,name"})
        return {"access_token": limpio["access_token"], "tipo": "usuario_sistema", "nombre": yo.get("name")}

    def necesita_refresco(self, datos):
        return False  # Meta no tiene refresh token: al caducar (60 días) hay que reconectar

    async def listar_cuentas(self, http, a, datos):
        token = datos["access_token"]
        cuentas = []
        for c in await paginar(http, a, "me/adaccounts", token,
                               {"fields": "id,name,currency,account_status", "limit": 200}):
            cuentas.append({"plataforma": "meta_ads", "cuenta_id": c["id"], "nombre": c.get("name"),
                            "extra": {"moneda": c.get("currency"), "estado": c.get("account_status")}})
        for p in await paginar(http, a, "me/accounts", token,
                               {"fields": "id,name,instagram_business_account{id,username}", "limit": 100}):
            cuentas.append({"plataforma": "facebook", "cuenta_id": p["id"], "nombre": p.get("name"), "extra": {}})
            ig = p.get("instagram_business_account")
            if ig:
                cuentas.append({"plataforma": "instagram", "cuenta_id": ig["id"],
                                "nombre": "@" + ig.get("username", ig["id"]),
                                "extra": {"pagina_id": p["id"], "usuario": ig.get("username")}})
        return cuentas


# ---------------------------------------------------------------- Meta Ads

class MetaAds(Plataforma):
    id = "meta_ads"
    nombre = "Meta Ads"
    tipo = "ads"
    login = "meta"

    async def metricas(self, ctx: Contexto, desde: date, hasta: date, opciones: dict) -> dict:
        a, token = ctx.ajustes, ctx.datos["access_token"]
        nivel = opcion(opciones.get("nivel", "campaign"), {"campaign": "campaign", "campana": "campaign",
                                                          "adset": "adset", "conjunto": "adset", "ad": "ad",
                                                          "anuncio": "ad", "account": "account", "cuenta": "account"},
                       "nivel")
        preferido = opciones.get("tipo_conversion") or a.meta_tipo_conversion
        filas, usados = [], set()
        for ini, fin in tramos(desde, hasta, 90):
            datos = await paginar(ctx.http, a, f"{ctx.cuenta_id}/insights", token, {
                "level": nivel, "time_increment": 1,
                "time_range": json.dumps({"since": ini.isoformat(), "until": fin.isoformat()}),
                "fields": "campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,spend,impressions,clicks,"
                          "actions,action_values,account_currency",
                "limit": 500}, "meta_ads")
            for d in datos:
                tipo = _elegir_tipo(d.get("actions"), preferido)
                if tipo:
                    usados.add(tipo)
                id_obj = d.get(f"{nivel}_id") if nivel != "account" else ctx.cuenta_id
                nombre = d.get(f"{nivel}_name") if nivel != "account" else None
                filas.append(fila_ads(d["date_start"], "meta_ads", ctx.cuenta_id, id_obj, nombre, d.get("spend"),
                                      d.get("impressions"), d.get("clicks"),
                                      _valor(d.get("actions"), tipo) if tipo else 0,
                                      _valor(d.get("action_values"), tipo) if tipo else 0,
                                      d.get("account_currency")))
        notas = [f"Conversiones e ingresos = acción '{', '.join(sorted(usados)) or 'ninguna con datos'}' "
                 "(una sola por fila, sin sumar tipos solapados)."]
        return {"filas": filas, "notas": notas}

    async def contenido(self, ctx, opciones):
        tipo = opcion(opciones.get("tipo") or "campanas", {
            "campanas": "campaigns", "campañas": "campaigns", "campaigns": "campaigns",
            "conjuntos": "adsets", "adsets": "adsets", "anuncios": "ads", "ads": "ads"}, "tipo")
        campos = {"campaigns": "id,name,status,effective_status,objective,daily_budget,lifetime_budget",
                  "adsets": "id,name,campaign_id,status,effective_status,optimization_goal,daily_budget,lifetime_budget",
                  "ads": "id,name,campaign_id,adset_id,status,effective_status"}[tipo]
        elementos = await paginar(ctx.http, ctx.ajustes, f"{ctx.cuenta_id}/{tipo}", ctx.datos["access_token"],
                                  {"fields": campos, "limit": 200}, "meta_ads")
        moneda = ctx.extra.get("moneda")
        for c in elementos:
            for k in ("daily_budget", "lifetime_budget"):
                if c.get(k):
                    c[k] = _de_minimo(c[k], moneda)
        nombre = {"campaigns": "campanas", "adsets": "conjuntos", "ads": "anuncios"}[tipo]
        return {"tipo": nombre, "moneda": moneda, "elementos": elementos}

    def acciones(self):
        return {
            "cambiar_estado": Accion(
                "cambiar_estado", "gestionar", "Pausa o activa una campaña, conjunto de anuncios o anuncio.",
                {"objeto_id": "ID de la campaña, conjunto o anuncio", "estado": "pausar | activar"},
                self._prev_estado, self._ejec_estado),
            "cambiar_presupuesto_diario": Accion(
                "cambiar_presupuesto_diario", "gestionar",
                "Cambia el presupuesto diario de una campaña (CBO) o de un conjunto de anuncios.",
                {"objeto_id": "ID de la campaña o del conjunto", "importe": "nuevo presupuesto diario en la moneda de la cuenta"},
                self._prev_presupuesto, self._ejec_presupuesto),
            "lanzar_campana": Accion(
                "lanzar_campana", "gestionar",
                "Crea una campaña completa EN PAUSA (campaña + conjunto de anuncios + anuncio con imagen). "
                "No gasta nada hasta que se active.", PARAMS_LANZAR, prev_lanzar, ejec_lanzar),
        }

    async def _objeto(self, ctx, objeto_id, campos):
        oid = str(objeto_id).strip()
        if not oid.isdigit():
            raise ErrorUsuario("objeto_id debe ser numérico")
        obj = await get(ctx.http, ctx.ajustes, oid, ctx.datos["access_token"], {"fields": campos + ",account_id"},
                        "meta_ads")
        if f"act_{obj.get('account_id')}" != ctx.cuenta_id:
            raise ErrorUsuario("Ese objeto no pertenece a la cuenta publicitaria de esta conexión")
        return obj

    async def _prev_estado(self, ctx, p):
        exigir(p, "objeto_id", "estado")
        nuevo = "PAUSED" if opcion(p["estado"], ESTADOS_ON_OFF, "estado") == "off" else "ACTIVE"
        obj = await self._objeto(ctx, p["objeto_id"], "name,status,effective_status")
        avisos = ["Activar empieza a gastar presupuesto."] if nuevo == "ACTIVE" else []
        return {"resumen": f"Meta Ads · '{obj.get('name')}': {obj.get('status')} → {nuevo}",
                "estado_actual": obj.get("status"), "estado_nuevo": nuevo, "avisos": avisos,
                "_params": {"objeto_id": str(p["objeto_id"]), "estado": nuevo}}

    async def _ejec_estado(self, ctx, p):
        r = await post(ctx.http, ctx.ajustes, p["objeto_id"], ctx.datos["access_token"], {"status": p["estado"]},
                       "meta_ads")
        return {"ok": bool(r.get("success", True)), "respuesta": r}

    async def _prev_presupuesto(self, ctx, p):
        exigir(p, "objeto_id", "importe")
        importe = importe_positivo(p["importe"])
        obj = await self._objeto(ctx, p["objeto_id"], "name,daily_budget,lifetime_budget")
        moneda = ctx.extra.get("moneda") or (await get(ctx.http, ctx.ajustes, ctx.cuenta_id, ctx.datos["access_token"],
                                                       {"fields": "currency"}, "meta_ads")).get("currency")
        if not obj.get("daily_budget"):
            raise ErrorUsuario("Este objeto no tiene presupuesto diario (puede tener presupuesto total o estar "
                               "el presupuesto en otro nivel).")
        actual = _de_minimo(obj["daily_budget"], moneda)
        return {"resumen": f"Meta Ads · '{obj.get('name')}': presupuesto diario {actual} → {importe} {moneda}",
                "cambio_presupuesto": {"actual": actual, "nuevo": importe, "moneda": moneda},
                "_params": {"objeto_id": str(p["objeto_id"]), "importe_minimo": _a_minimo(importe, moneda)}}

    async def _ejec_presupuesto(self, ctx, p):
        r = await post(ctx.http, ctx.ajustes, p["objeto_id"], ctx.datos["access_token"],
                       {"daily_budget": str(p["importe_minimo"])}, "meta_ads")
        return {"ok": bool(r.get("success", True)), "respuesta": r}


def _elegir_tipo(acciones: list | None, preferido: str | None) -> str | None:
    tipos = {x.get("action_type") for x in (acciones or [])}
    if preferido:
        return preferido if preferido in tipos else None
    for t in PRIORIDAD_CONVERSION:
        if t in tipos:
            return t
    return None


def _valor(lista: list | None, tipo: str) -> float:
    for x in lista or []:
        if x.get("action_type") == tipo:
            return num(x.get("value"))
    return 0.0


def _de_minimo(valor, moneda) -> float:
    return num(valor) if (moneda or "").upper() in SIN_DECIMALES else round(num(valor) / 100, 2)


def _a_minimo(importe: float, moneda) -> int:
    return int(round(importe)) if (moneda or "").upper() in SIN_DECIMALES else int(round(importe * 100))


# ---------------------------------------------------------------- Página de Facebook

METRICAS_PAGINA = ["page_post_engagements", "page_follows", "page_views_total"]


async def token_pagina(ctx: Contexto, pagina_id: str) -> str:
    r = await get(ctx.http, ctx.ajustes, pagina_id, ctx.datos["access_token"], {"fields": "access_token"}, "facebook")
    if not r.get("access_token"):
        raise ErrorPlataforma("facebook", "No se pudo obtener el token de la página: ¿tienes permisos sobre ella?")
    return r["access_token"]


async def insights_por_metrica(ctx: Contexto, objeto_id: str, token: str, metricas: list[str], desde: date,
                               hasta: date, plataforma: str, total_value: set[str] = frozenset()) -> dict:
    """Pide cada métrica por separado para que una métrica retirada no tumbe las demás."""
    filas, errores = [], {}
    for m in metricas:
        for ini, fin in tramos(desde, hasta, 29):
            # 'until' es exclusivo: se pide un día más para incluir 'fin'
            params = {"metric": m, "period": "day", "since": ini.isoformat(),
                      "until": (fin + timedelta(days=1)).isoformat()}
            if m in total_value:
                params["metric_type"] = "total_value"
            try:
                res = await get(ctx.http, ctx.ajustes, f"{objeto_id}/insights", token, params, plataforma)
            except ErrorPlataforma as e:
                errores[m] = e.mensaje
                break
            for serie in res.get("data", []):
                if "total_value" in serie:
                    filas.append({"fecha": ini.isoformat(), "fecha_fin": fin.isoformat(), "plataforma": plataforma,
                                  "cuenta_id": objeto_id, "metrica": m, "periodo": "total",
                                  "valor": (serie["total_value"] or {}).get("value")})
                for v in serie.get("values", []):
                    # end_time marca el final del día: el dato corresponde al día anterior
                    fecha = _dia_de(v.get("end_time"))
                    if isinstance(v.get("value"), dict):
                        for sub, val in v["value"].items():
                            filas.append({"fecha": fecha, "plataforma": plataforma, "cuenta_id": objeto_id,
                                          "metrica": f"{m}.{sub}", "valor": val})
                    else:
                        filas.append({"fecha": fecha, "plataforma": plataforma, "cuenta_id": objeto_id,
                                      "metrica": m, "valor": v.get("value")})
    notas = ["Los días siguen el horario de Meta (Pacífico), como en sus paneles."]
    if errores:
        notas.append("Algunas métricas no están disponibles en esta versión de la API: " +
                     "; ".join(f"{k}: {v}" for k, v in errores.items()))
    return {"filas": filas, "notas": notas, "errores_metricas": errores}


def _dia_de(end_time: str | None) -> str | None:
    if not end_time:
        return None
    return (date.fromisoformat(end_time[:10]) - timedelta(days=1)).isoformat()


class Facebook(Plataforma):
    id = "facebook"
    nombre = "Página de Facebook"
    tipo = "social"
    login = "meta"

    async def metricas(self, ctx, desde, hasta, opciones):
        metricas = opciones.get("metricas") or METRICAS_PAGINA
        if isinstance(metricas, str):
            metricas = [m.strip() for m in metricas.split(",") if m.strip()]
        token = await token_pagina(ctx, ctx.cuenta_id)
        return await insights_por_metrica(ctx, ctx.cuenta_id, token, metricas, desde, hasta, "facebook")

    async def contenido(self, ctx, opciones):
        token = await token_pagina(ctx, ctx.cuenta_id)
        res = await get(ctx.http, ctx.ajustes, f"{ctx.cuenta_id}/posts", token, {
            "fields": "id,message,created_time,permalink_url", "limit": int(opciones.get("limite", 25))}, "facebook")
        return {"tipo": "publicaciones", "elementos": res.get("data", [])}

    def acciones(self):
        return {"publicar": Accion(
            "publicar", "publicar", "Publica (o programa) un post en la página, con enlace o imagen opcional.",
            {"texto": "texto del post", "enlace": "URL a compartir (opcional)",
             "imagen_url": "URL pública de una imagen (opcional)",
             "programar_para": "fecha y hora ISO con zona, p. ej. 2026-11-20T10:00:00+01:00 (opcional, 10 min a 30 días)"},
            self._prev, self._ejec)}

    async def _prev(self, ctx, p):
        exigir(p, "texto")
        if p.get("enlace") and p.get("imagen_url"):
            raise ErrorUsuario("Usa enlace o imagen_url, no los dos.")
        ts = _programacion(p.get("programar_para"))
        pagina = await get(ctx.http, ctx.ajustes, ctx.cuenta_id, ctx.datos["access_token"], {"fields": "name"},
                           "facebook")
        cuando = f"programado para {p['programar_para']}" if ts else "publicación inmediata"
        return {"resumen": f"Facebook · {pagina.get('name')}: {cuando}",
                "contenido": {k: p.get(k) for k in ("texto", "enlace", "imagen_url") if p.get(k)},
                "_params": {**{k: p.get(k) for k in ("texto", "enlace", "imagen_url")}, "ts": ts}}

    async def _ejec(self, ctx, p):
        token = await token_pagina(ctx, ctx.cuenta_id)
        extra = {"published": "false", "scheduled_publish_time": str(p["ts"])} if p.get("ts") else {}
        if p.get("imagen_url"):
            r = await post(ctx.http, ctx.ajustes, f"{ctx.cuenta_id}/photos", token,
                           {"url": p["imagen_url"], "caption": p["texto"], **extra}, "facebook")
        else:
            datos = {"message": p["texto"], **extra}
            if p.get("enlace"):
                datos["link"] = p["enlace"]
            r = await post(ctx.http, ctx.ajustes, f"{ctx.cuenta_id}/feed", token, datos, "facebook")
        return {"ok": True, "id": r.get("post_id") or r.get("id"), "respuesta": r}


def _programacion(valor) -> int | None:
    if not valor:
        return None
    try:
        dt = datetime.fromisoformat(str(valor))
    except ValueError:
        raise ErrorUsuario("programar_para debe ser ISO 8601, p. ej. 2026-11-20T10:00:00+01:00") from None
    if dt.tzinfo is None:
        raise ErrorUsuario("programar_para debe incluir zona horaria (p. ej. +01:00)")
    segundos = (dt - datetime.now(timezone.utc)).total_seconds()
    if not 600 <= segundos <= 30 * 86400:
        raise ErrorUsuario("Meta solo permite programar entre 10 minutos y 30 días vista")
    return int(dt.timestamp())


# ---------------------------------------------------------------- Instagram

METRICAS_IG = ["reach", "follower_count", "views", "accounts_engaged", "total_interactions"]
IG_TOTAL_VALUE = {"views", "accounts_engaged", "total_interactions"}


class Instagram(Plataforma):
    id = "instagram"
    nombre = "Instagram"
    tipo = "social"
    login = "meta"

    async def metricas(self, ctx, desde, hasta, opciones):
        metricas = opciones.get("metricas") or METRICAS_IG
        if isinstance(metricas, str):
            metricas = [m.strip() for m in metricas.split(",") if m.strip()]
        res = await insights_por_metrica(ctx, ctx.cuenta_id, ctx.datos["access_token"], metricas, desde, hasta,
                                         "instagram", IG_TOTAL_VALUE)
        res["notas"].append("views, accounts_engaged y total_interactions son totales por tramo (Meta no los da por día).")
        return res

    async def contenido(self, ctx, opciones):
        res = await get(ctx.http, ctx.ajustes, f"{ctx.cuenta_id}/media", ctx.datos["access_token"], {
            "fields": "id,caption,media_type,permalink,timestamp,like_count,comments_count",
            "limit": int(opciones.get("limite", 25))}, "instagram")
        return {"tipo": "publicaciones", "elementos": res.get("data", [])}

    def acciones(self):
        return {"publicar": Accion(
            "publicar", "publicar", "Publica una imagen, un reel o un carrusel en Instagram.",
            {"tipo": "imagen | reel | carrusel", "texto": "pie de foto",
             "media_url": "URL pública del archivo (imagen o vídeo)",
             "media_urls": "lista de URLs para carrusel (2 a 10, solo imágenes)"},
            self._prev, self._ejec)}

    async def _prev(self, ctx, p):
        exigir(p, "tipo")
        tipo = opcion(p["tipo"], {"imagen": "imagen", "image": "imagen", "foto": "imagen", "reel": "reel",
                                  "reels": "reel", "video": "reel", "carrusel": "carrusel", "carousel": "carrusel"},
                      "tipo")
        if tipo == "carrusel":
            urls = p.get("media_urls") or []
            if isinstance(urls, str):
                urls = [u.strip() for u in urls.split(",") if u.strip()]
            if not 2 <= len(urls) <= 10:
                raise ErrorUsuario("Un carrusel necesita entre 2 y 10 imágenes en media_urls")
        else:
            exigir(p, "media_url")
            urls = [p["media_url"]]
        if any(not str(u).startswith("https://") for u in urls):
            raise ErrorUsuario("Las URLs deben ser públicas y empezar por https://")
        return {"resumen": f"Instagram · {ctx.extra.get('usuario') or ctx.cuenta_id}: publicar {tipo}",
                "contenido": {"tipo": tipo, "texto": p.get("texto", ""), "archivos": urls},
                "_params": {"tipo": tipo, "texto": p.get("texto", ""), "urls": urls}}

    async def _ejec(self, ctx, p):
        a, token, ig = ctx.ajustes, ctx.datos["access_token"], ctx.cuenta_id
        if p["tipo"] == "imagen":
            cont = await post(ctx.http, a, f"{ig}/media", token, {"image_url": p["urls"][0], "caption": p["texto"]},
                              "instagram")
        elif p["tipo"] == "reel":
            cont = await post(ctx.http, a, f"{ig}/media", token, {"media_type": "REELS", "video_url": p["urls"][0],
                                                                  "caption": p["texto"]}, "instagram")
        else:
            hijos = []
            for u in p["urls"]:
                h = await post(ctx.http, a, f"{ig}/media", token, {"image_url": u, "is_carousel_item": "true"},
                               "instagram")
                hijos.append(h["id"])
            cont = await post(ctx.http, a, f"{ig}/media", token, {"media_type": "CAROUSEL",
                                                                  "children": ",".join(hijos), "caption": p["texto"]},
                              "instagram")
        await self._esperar(ctx, cont["id"])
        pub = await post(ctx.http, a, f"{ig}/media_publish", token, {"creation_id": cont["id"]}, "instagram")
        return {"ok": True, "id": pub.get("id"), "contenedor": cont["id"]}

    async def _esperar(self, ctx, contenedor: str, max_seg: int = 300) -> None:
        for _ in range(max_seg // 5):
            r = await get(ctx.http, ctx.ajustes, contenedor, ctx.datos["access_token"], {"fields": "status_code"},
                          "instagram")
            estado = r.get("status_code")
            if estado in (None, "FINISHED", "PUBLISHED"):
                return
            if estado in ("ERROR", "EXPIRED"):
                raise ErrorPlataforma("instagram", f"Instagram no pudo procesar el archivo (estado {estado})")
            await asyncio.sleep(5)
        raise ErrorPlataforma("instagram", "El vídeo sigue procesándose tras 5 minutos; reinténtalo más tarde")


from .meta_campanas import PARAMS_LANZAR, ejec_lanzar, prev_lanzar  # noqa: E402
