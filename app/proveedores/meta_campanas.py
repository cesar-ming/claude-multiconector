"""Meta Ads: lanzar una campaña completa (campaña + conjunto + anuncio), siempre EN PAUSA."""
from __future__ import annotations

import base64
import json
from datetime import datetime

from ..http import ErrorPlataforma, ErrorUsuario, peticion
from .base import Contexto, exigir, importe_positivo, opcion
from .meta import _a_minimo, get, post

# objetivo -> (objetivo de campaña, objetivo de optimización, evento del píxel)
OBJETIVOS = {
    "ventas": ("OUTCOME_SALES", "OFFSITE_CONVERSIONS", "PURCHASE"),
    "clientes_potenciales": ("OUTCOME_LEADS", "OFFSITE_CONVERSIONS", "LEAD"),
    "trafico": ("OUTCOME_TRAFFIC", "LINK_CLICKS", None),
    "reconocimiento": ("OUTCOME_AWARENESS", "REACH", None),
}
ALIAS_OBJETIVO = {"ventas": "ventas", "sales": "ventas", "compras": "ventas",
                  "clientes_potenciales": "clientes_potenciales", "leads": "clientes_potenciales",
                  "trafico": "trafico", "tráfico": "trafico", "traffic": "trafico",
                  "reconocimiento": "reconocimiento", "alcance": "reconocimiento", "awareness": "reconocimiento"}
BOTONES = {"comprar": "SHOP_NOW", "mas_informacion": "LEARN_MORE", "más_información": "LEARN_MORE",
           "registrarse": "SIGN_UP", "contactar": "CONTACT_US", "pedir_presupuesto": "GET_QUOTE",
           "reservar": "BOOK_NOW", "descargar": "DOWNLOAD", "suscribirse": "SUBSCRIBE",
           "shop_now": "SHOP_NOW", "learn_more": "LEARN_MORE", "sign_up": "SIGN_UP"}

PARAMS_LANZAR = {
    "nombre": "nombre de la campaña",
    "objetivo": "ventas | clientes_potenciales | trafico | reconocimiento",
    "presupuesto_diario": "presupuesto diario de la campaña en la moneda de la cuenta",
    "url": "página de destino (https://…)",
    "texto_principal": "texto principal del anuncio",
    "titular": "titular (máx. ~40 caracteres recomendados)",
    "descripcion": "descripción breve (opcional)",
    "imagen_url": "URL pública https de la imagen del anuncio (JPG o PNG)",
    "boton": "comprar | mas_informacion | registrarse | contactar | pedir_presupuesto | reservar | descargar | suscribirse",
    "paises": "lista de códigos de país, por defecto [\"ES\"]",
    "publico_advantage": "true (por defecto: Meta amplía el público) o false (solo la segmentación indicada)",
    "edad_min": "edad mínima (18-65; con público Advantage solo 18-25)",
    "edad_max": "edad máxima (solo si publico_advantage es false)",
    "pixel_id": "píxel para ventas o clientes potenciales (si hay uno solo en la cuenta, se elige solo)",
    "pagina_id": "página de Facebook que firma el anuncio (si hay una sola, se elige sola)",
    "instagram_id": "cuenta de Instagram que firma el anuncio (opcional)",
    "fecha_inicio": "fecha y hora ISO con zona para empezar al activarla (opcional)",
}


def _lista(valor, por_defecto: list[str]) -> list[str]:
    if not valor:
        return por_defecto
    if isinstance(valor, str):
        valor = [v.strip() for v in valor.split(",")]
    return [str(v).strip().upper() for v in valor if str(v).strip()]


def _bool(valor, por_defecto: bool) -> bool:
    if valor is None or valor == "":
        return por_defecto
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in ("1", "true", "si", "sí", "yes")


async def _unico(ctx: Contexto, ruta: str, que: str, valor: str | None) -> dict:
    """Comprueba el ID indicado o elige el único disponible en la cuenta."""
    lista = (await get(ctx.http, ctx.ajustes, f"{ctx.cuenta_id}/{ruta}", ctx.datos["access_token"],
                       {"fields": "id,name", "limit": 100}, "meta_ads")).get("data", [])
    if valor:
        elegido = next((x for x in lista if str(x["id"]) == str(valor)), None)
        if not elegido:
            opciones = ", ".join(f"{x.get('name')} ({x['id']})" for x in lista) or "ninguno"
            raise ErrorUsuario(f"El {que} {valor} no está disponible para esta cuenta publicitaria. "
                               f"Disponibles: {opciones}")
        return elegido
    if len(lista) == 1:
        return lista[0]
    if not lista:
        raise ErrorUsuario(f"La cuenta publicitaria no tiene ningún {que} disponible")
    raise ErrorUsuario(f"Hay varios {que}s: indica cuál. Opciones: "
                       + ", ".join(f"{x.get('name')} ({x['id']})" for x in lista))


async def prev_lanzar(ctx: Contexto, p: dict) -> dict:
    exigir(p, "nombre", "objetivo", "presupuesto_diario", "url", "texto_principal", "titular", "imagen_url")
    objetivo = opcion(p["objetivo"], ALIAS_OBJETIVO, "objetivo")
    obj_campana, optimizacion, evento = OBJETIVOS[objetivo]
    importe = importe_positivo(p["presupuesto_diario"], "presupuesto_diario")
    for campo in ("url", "imagen_url"):
        if not str(p[campo]).startswith("https://"):
            raise ErrorUsuario(f"{campo} debe empezar por https://")
    boton = opcion(p.get("boton") or ("comprar" if objetivo == "ventas" else "mas_informacion"), BOTONES, "boton")
    paises = _lista(p.get("paises"), ["ES"])
    advantage = _bool(p.get("publico_advantage"), True)
    edad_min = int(p.get("edad_min") or 18)
    if advantage and not 18 <= edad_min <= 25:
        raise ErrorUsuario("Con público Advantage la edad mínima debe estar entre 18 y 25. "
                           "Usa publico_advantage=false para segmentar por edad.")
    targeting = {"geo_locations": {"countries": paises}, "age_min": edad_min,
                 "targeting_automation": {"advantage_audience": 1 if advantage else 0}}
    if not advantage:
        edad_max = int(p.get("edad_max") or 65)
        if not 18 <= edad_min <= edad_max <= 65:
            raise ErrorUsuario("Edades no válidas: 18 ≤ edad_min ≤ edad_max ≤ 65")
        targeting["age_max"] = edad_max
    inicio = None
    if p.get("fecha_inicio"):
        try:
            inicio = datetime.fromisoformat(str(p["fecha_inicio"]))
        except ValueError:
            raise ErrorUsuario("fecha_inicio debe ser ISO, p. ej. 2026-12-01T09:00:00+01:00") from None
        if inicio.tzinfo is None:
            raise ErrorUsuario("fecha_inicio debe incluir la zona horaria (p. ej. +01:00)")

    cuenta = await get(ctx.http, ctx.ajustes, ctx.cuenta_id, ctx.datos["access_token"],
                       {"fields": "currency,name"}, "meta_ads")
    moneda = cuenta.get("currency") or ctx.extra.get("moneda")
    pagina = await _unico(ctx, "promote_pages", "página", p.get("pagina_id"))
    pixel = await _unico(ctx, "adspixels", "píxel", p.get("pixel_id")) if evento else None

    # Meta valida la campaña sin crearla
    await post(ctx.http, ctx.ajustes, f"{ctx.cuenta_id}/campaigns", ctx.datos["access_token"], {
        "name": p["nombre"], "objective": obj_campana, "status": "PAUSED", "special_ad_categories": "[]",
        "daily_budget": str(_a_minimo(importe, moneda)), "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "execution_options": '["validate_only"]'}, "meta_ads")

    estructura = {
        "campana": {"nombre": p["nombre"], "objetivo": obj_campana, "presupuesto_diario": f"{importe} {moneda}",
                    "estado": "EN PAUSA"},
        "conjunto": {"optimizacion": optimizacion, "evento": evento, "pixel": pixel and pixel.get("name"),
                     "paises": paises, "edad_min": edad_min, "edad_max": targeting.get("age_max", 65),
                     "publico_advantage": advantage, "inicio": inicio.isoformat() if inicio else "al activarla"},
        "anuncio": {"pagina": pagina.get("name"), "instagram": p.get("instagram_id") or "no",
                    "texto_principal": p["texto_principal"], "titular": p["titular"],
                    "descripcion": p.get("descripcion", ""), "boton": boton, "url": p["url"],
                    "imagen": p["imagen_url"]},
    }
    avisos = ["Todo se crea EN PAUSA: no gasta hasta que lo actives (con otra acción y tu aprobación).",
              "El escaparate automático de productos del catálogo queda desactivado."]
    if advantage:
        avisos.append("Público Advantage activado: Meta puede mostrar el anuncio fuera de la segmentación "
                      "indicada (respeta países y edad mínima).")
    return {
        "resumen": f"Meta Ads · nueva campaña '{p['nombre']}' ({objetivo}, {importe} {moneda}/día) EN PAUSA",
        "estructura": estructura, "avisos": avisos,
        "cambio_presupuesto": {"actual": None, "nuevo": importe, "moneda": moneda},
        "_params": {"nombre": p["nombre"], "objetivo_campana": obj_campana, "optimizacion": optimizacion,
                    "evento": evento, "pixel_id": pixel and pixel["id"], "pagina_id": pagina["id"],
                    "instagram_id": p.get("instagram_id"), "presupuesto_minimo": _a_minimo(importe, moneda),
                    "targeting": targeting, "inicio": inicio.isoformat() if inicio else None,
                    "url": p["url"], "texto": p["texto_principal"], "titular": p["titular"],
                    "descripcion": p.get("descripcion", ""), "boton": boton, "imagen_url": p["imagen_url"]},
    }


async def _subir_imagen(ctx: Contexto, url: str) -> str:
    r = await peticion(ctx.http, "meta_ads", "GET", url)
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
        raise ErrorUsuario(f"No se pudo descargar la imagen ({r.status_code}) o no es una imagen")
    if len(r.content) > 30 * 1024 * 1024:
        raise ErrorUsuario("La imagen supera 30 MB")
    res = await post(ctx.http, ctx.ajustes, f"{ctx.cuenta_id}/adimages", ctx.datos["access_token"],
                     {"bytes": base64.b64encode(r.content).decode()}, "meta_ads")
    imagenes = res.get("images") or {}
    for datos in imagenes.values():
        if datos.get("hash"):
            return datos["hash"]
    raise ErrorPlataforma("meta_ads", "Meta no devolvió el identificador de la imagen")


async def ejec_lanzar(ctx: Contexto, p: dict) -> dict:
    a, token, act = ctx.ajustes, ctx.datos["access_token"], ctx.cuenta_id
    creados: dict[str, str] = {}
    try:
        imagen = await _subir_imagen(ctx, p["imagen_url"])
        camp = await post(ctx.http, a, f"{act}/campaigns", token, {
            "name": p["nombre"], "objective": p["objetivo_campana"], "status": "PAUSED",
            "special_ad_categories": "[]", "daily_budget": str(p["presupuesto_minimo"]),
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP"}, "meta_ads")
        creados["campana"] = camp["id"]
        conjunto = {"name": f"{p['nombre']} · conjunto", "campaign_id": camp["id"], "status": "PAUSED",
                    "optimization_goal": p["optimizacion"], "billing_event": "IMPRESSIONS",
                    "targeting": json.dumps(p["targeting"])}
        if p.get("evento"):
            conjunto["promoted_object"] = json.dumps({"pixel_id": p["pixel_id"], "custom_event_type": p["evento"]})
        if p.get("inicio"):
            conjunto["start_time"] = p["inicio"]
        adset = await post(ctx.http, a, f"{act}/adsets", token, conjunto, "meta_ads")
        creados["conjunto"] = adset["id"]
        link_data = {"link": p["url"], "message": p["texto"], "name": p["titular"], "image_hash": imagen,
                     "call_to_action": {"type": p["boton"], "value": {"link": p["url"]}}}
        if p.get("descripcion"):
            link_data["description"] = p["descripcion"]
        historia = {"page_id": p["pagina_id"], "link_data": link_data}
        if p.get("instagram_id"):
            historia["instagram_user_id"] = p["instagram_id"]
        creatividad = await post(ctx.http, a, f"{act}/adcreatives", token, {
            "name": f"{p['nombre']} · creatividad", "object_story_spec": json.dumps(historia),
            "degrees_of_freedom_spec": json.dumps({"creative_features_spec": {"product_extensions": {
                "enroll_status": "OPT_OUT", "action_metadata": {"type": "MANUAL"}}}})}, "meta_ads")
        creados["creatividad"] = creatividad["id"]
        anuncio = await post(ctx.http, a, f"{act}/ads", token, {
            "name": f"{p['nombre']} · anuncio", "adset_id": adset["id"], "status": "PAUSED",
            "creative": json.dumps({"creative_id": creatividad["id"]})}, "meta_ads")
        creados["anuncio"] = anuncio["id"]
    except (ErrorPlataforma, ErrorUsuario) as e:
        limpieza = "nada que limpiar"
        if creados.get("campana"):
            try:
                await post(ctx.http, a, creados["campana"], token, {"status": "DELETED"}, "meta_ads")
                limpieza = f"campaña {creados['campana']} eliminada para no dejar restos"
            except ErrorPlataforma:
                limpieza = f"no se pudo eliminar la campaña {creados['campana']}: revísala en el Administrador de anuncios"
        raise ErrorPlataforma("meta_ads", f"{e} ({limpieza})", detalle={"creados": creados}) from e
    cuenta_num = act.removeprefix("act_")
    return {"ok": True, "estado": "EN PAUSA", **creados,
            "enlace": f"https://adsmanager.facebook.com/adsmanager/manage/campaigns?act={cuenta_num}"
                      f"&selected_campaign_ids={creados['campana']}"}
