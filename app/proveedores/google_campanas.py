"""Google Ads: crear campañas de búsqueda EN PAUSA y acciones de optimización.

Todas las altas van en una sola llamada a GoogleAdsService.Mutate: o se crea todo o no se crea nada.
La vista previa usa validateOnly, así que Google valida la estructura real sin crear nada.
"""
from __future__ import annotations

import re

from ..http import ErrorUsuario, json_o_error, peticion
from .base import Contexto, exigir, importe_positivo, num, opcion, solo_digitos
from .google_ads import base, cabeceras, gaql

ESPANA, ESPANOL = "2724", "1003"  # geoTargetConstants/2724 (España) · languageConstants/1003 (español)
CONCORDANCIAS = {"amplia": "BROAD", "broad": "BROAD", "frase": "PHRASE", "phrase": "PHRASE",
                 "exacta": "EXACT", "exact": "EXACT"}
ESTRATEGIAS = {"maximizar_conversiones": "maximizeConversions", "maximizar_clics": "targetSpend",
               "cpc_manual": "manualCpc"}

PARAMS_BUSQUEDA = {
    "nombre": "nombre de la campaña",
    "presupuesto_diario": "presupuesto diario en la moneda de la cuenta",
    "url_final": "página de destino (https://…)",
    "palabras_clave": "lista de palabras clave; admite \"frase\", [exacta] o texto suelto (usa la concordancia por defecto)",
    "concordancia": "amplia | frase | exacta (por defecto frase)",
    "titulares": "lista de 3 a 15 titulares (máx. 30 caracteres cada uno)",
    "descripciones": "lista de 2 a 4 descripciones (máx. 90 caracteres cada una)",
    "ruta1": "texto de la URL visible (opcional, máx. 15)",
    "ruta2": "segundo texto de la URL visible (opcional, máx. 15)",
    "negativas": "palabras clave negativas de la campaña (opcional)",
    "estrategia": "maximizar_conversiones (por defecto) | maximizar_clics | cpc_manual",
    "cpc_max": "CPC máximo del grupo, solo con cpc_manual",
    "ubicaciones": "IDs de ubicación de Google (por defecto 2724 = España)",
    "idiomas": "IDs de idioma de Google (por defecto 1003 = español)",
}


def _lista(valor) -> list[str]:
    if not valor:
        return []
    if isinstance(valor, str):
        valor = [v for v in re.split(r"[\n;]|,(?![^\[]*\])", valor)]
    return [str(v).strip() for v in valor if str(v).strip()]


def palabras(valores, concordancia_defecto: str) -> list[dict]:
    """Convierte 'texto', '"frase"' o '[exacta]' en criterios de palabra clave."""
    salida, vistas = [], set()
    for v in _lista(valores):
        tipo = concordancia_defecto
        if v.startswith("[") and v.endswith("]"):
            v, tipo = v[1:-1], "EXACT"
        elif v.startswith('"') and v.endswith('"'):
            v, tipo = v[1:-1], "PHRASE"
        v = re.sub(r"\s+", " ", v).strip()
        if not v:
            continue
        if len(v) > 80 or len(v.split()) > 10:
            raise ErrorUsuario(f"Palabra clave demasiado larga (máx. 80 caracteres y 10 palabras): {v!r}")
        if (v.lower(), tipo) not in vistas:
            vistas.add((v.lower(), tipo))
            salida.append({"text": v, "matchType": tipo})
    return salida


def _textos(valores, minimo: int, maximo: int, largo: int, que: str) -> list[str]:
    lista = _lista(valores)
    if not minimo <= len(lista) <= maximo:
        raise ErrorUsuario(f"Hacen falta entre {minimo} y {maximo} {que} (hay {len(lista)})")
    largos = [t for t in lista if len(t) > largo]
    if largos:
        raise ErrorUsuario(f"Estos {que} superan {largo} caracteres: " + "; ".join(f"{t!r} ({len(t)})" for t in largos))
    if len({t.lower() for t in lista}) != len(lista):
        raise ErrorUsuario(f"Hay {que} repetidos")
    return lista


async def _mutate(ctx: Contexto, operaciones: list[dict], validar: bool) -> dict:
    a = ctx.ajustes
    r = await peticion(ctx.http, "google_ads", "POST", f"{base(a)}/customers/{ctx.cuenta_id}/googleAds:mutate",
                       intentos=1, headers=cabeceras(a, ctx.datos["access_token"], ctx.extra.get("login_customer_id")),
                       json={"mutateOperations": operaciones, "validateOnly": validar, "partialFailure": False})
    return json_o_error(r, "google_ads")


def _operaciones_busqueda(cid: str, p: dict) -> list[dict]:
    rn_presu, rn_camp, rn_grupo = (f"customers/{cid}/campaignBudgets/-1", f"customers/{cid}/campaigns/-2",
                                   f"customers/{cid}/adGroups/-3")
    campana = {"resourceName": rn_camp, "name": p["nombre"], "status": "PAUSED",
               "advertisingChannelType": "SEARCH", "campaignBudget": rn_presu,
               "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
               "networkSettings": {"targetGoogleSearch": True, "targetSearchNetwork": False,
                                   "targetContentNetwork": False, "targetPartnerSearchNetwork": False},
               "geoTargetTypeSetting": {"positiveGeoTargetType": "PRESENCE"},
               p["estrategia"]: {}}
    grupo = {"resourceName": rn_grupo, "name": f"{p['nombre']} · grupo 1", "campaign": rn_camp,
             "status": "ENABLED", "type": "SEARCH_STANDARD"}
    if p.get("cpc_micros"):
        grupo["cpcBidMicros"] = p["cpc_micros"]
    ops = [
        {"campaignBudgetOperation": {"create": {"resourceName": rn_presu, "name": f"{p['nombre']} · presupuesto",
                                                "amountMicros": p["micros"], "deliveryMethod": "STANDARD",
                                                "explicitlyShared": False}}},
        {"campaignOperation": {"create": campana}},
    ]
    ops += [{"campaignCriterionOperation": {"create": {"campaign": rn_camp, "location": {
        "geoTargetConstant": f"geoTargetConstants/{u}"}}}} for u in p["ubicaciones"]]
    ops += [{"campaignCriterionOperation": {"create": {"campaign": rn_camp, "language": {
        "languageConstant": f"languageConstants/{i}"}}}} for i in p["idiomas"]]
    ops += [{"campaignCriterionOperation": {"create": {"campaign": rn_camp, "negative": True, "keyword": k}}}
            for k in p["negativas"]]
    ops.append({"adGroupOperation": {"create": grupo}})
    ops += [{"adGroupCriterionOperation": {"create": {"adGroup": rn_grupo, "status": "ENABLED", "keyword": k}}}
            for k in p["palabras"]]
    anuncio = {"finalUrls": [p["url_final"]], "responsiveSearchAd": {
        "headlines": [{"text": t} for t in p["titulares"]],
        "descriptions": [{"text": t} for t in p["descripciones"]]}}
    for k in ("path1", "path2"):
        if p.get(k):
            anuncio["responsiveSearchAd"][k] = p[k]
    ops.append({"adGroupAdOperation": {"create": {"adGroup": rn_grupo, "status": "ENABLED", "ad": anuncio}}})
    return ops


async def prev_busqueda(ctx: Contexto, p: dict) -> dict:
    exigir(p, "nombre", "presupuesto_diario", "url_final", "palabras_clave", "titulares", "descripciones")
    importe = importe_positivo(p["presupuesto_diario"], "presupuesto_diario")
    if not str(p["url_final"]).startswith("https://"):
        raise ErrorUsuario("url_final debe empezar por https://")
    concordancia = opcion(p.get("concordancia") or "frase", CONCORDANCIAS, "concordancia")
    claves = palabras(p["palabras_clave"], concordancia)
    if not claves:
        raise ErrorUsuario("Indica al menos una palabra clave")
    negativas = palabras(p.get("negativas"), "PHRASE")
    titulares = _textos(p["titulares"], 3, 15, 30, "titulares")
    descripciones = _textos(p["descripciones"], 2, 4, 90, "descripciones")
    for k in ("ruta1", "ruta2"):
        if p.get(k) and len(str(p[k])) > 15:
            raise ErrorUsuario(f"{k} admite como mucho 15 caracteres")
    estrategia = opcion(p.get("estrategia") or "maximizar_conversiones", ESTRATEGIAS, "estrategia")
    cpc_micros = None
    if estrategia == "manualCpc":
        cpc_micros = str(int(round(importe_positivo(p.get("cpc_max"), "cpc_max") * 1_000_000)))
    ubicaciones = [solo_digitos(u, "ubicación") for u in (_lista(p.get("ubicaciones")) or [ESPANA])]
    idiomas = [solo_digitos(i, "idioma") for i in (_lista(p.get("idiomas")) or [ESPANOL])]
    params = {"nombre": p["nombre"], "micros": str(int(round(importe * 1_000_000))), "url_final": p["url_final"],
              "palabras": claves, "negativas": negativas, "titulares": titulares, "descripciones": descripciones,
              "path1": p.get("ruta1"), "path2": p.get("ruta2"), "estrategia": estrategia, "cpc_micros": cpc_micros,
              "ubicaciones": ubicaciones, "idiomas": idiomas}
    # Google valida toda la estructura sin crear nada
    await _mutate(ctx, _operaciones_busqueda(ctx.cuenta_id, params), validar=True)
    moneda = ctx.extra.get("moneda")
    nombres_estrategia = {"maximizeConversions": "maximizar conversiones", "targetSpend": "maximizar clics",
                          "manualCpc": "CPC manual"}
    avisos = ["Todo se crea EN PAUSA: no gasta hasta que lo actives (con otra acción y tu aprobación).",
              "Google ha validado la estructura completa (presupuesto, campaña, segmentación, palabras y anuncio)."]
    if estrategia == "maximizeConversions":
        avisos.append("Maximizar conversiones necesita conversiones bien medidas en la cuenta; si no las hay, "
                      "considera maximizar_clics al principio.")
    return {
        "resumen": f"Google Ads · nueva campaña de búsqueda '{p['nombre']}' ({importe} {moneda or ''}/día) EN PAUSA",
        "estructura": {
            "campana": {"nombre": p["nombre"], "presupuesto_diario": importe, "moneda": moneda,
                        "estrategia": nombres_estrategia[estrategia], "red": "solo Búsqueda de Google",
                        "ubicaciones": ubicaciones, "idiomas": idiomas,
                        "negativas": [k["text"] for k in negativas]},
            "grupo": {"palabras_clave": [f"{k['text']} ({k['matchType'].lower()})" for k in claves],
                      "cpc_max": num(cpc_micros) / 1_000_000 if cpc_micros else None},
            "anuncio": {"url_final": p["url_final"], "titulares": titulares, "descripciones": descripciones,
                        "ruta": "/".join(x for x in (p.get("ruta1"), p.get("ruta2")) if x)}},
        "avisos": avisos,
        "cambio_presupuesto": {"actual": None, "nuevo": importe, "moneda": moneda},
        "_params": params,
    }


async def ejec_busqueda(ctx: Contexto, p: dict) -> dict:
    res = await _mutate(ctx, _operaciones_busqueda(ctx.cuenta_id, p), validar=False)
    resultados = res.get("mutateOperationResponses", [])
    camp = next((r["campaignResult"]["resourceName"] for r in resultados if "campaignResult" in r), "")
    grupo = next((r["adGroupResult"]["resourceName"] for r in resultados if "adGroupResult" in r), "")
    campana_id = camp.split("/")[-1]
    return {"ok": True, "estado": "EN PAUSA", "campana_id": campana_id, "grupo_id": grupo.split("/")[-1],
            "operaciones": len(resultados),
            "enlace": f"https://ads.google.com/aw/campaigns?campaignId={campana_id}"}


# ---------------------------------------------------------------- optimización

PARAMS_PALABRAS = {"grupo_id": "ID del grupo de anuncios", "palabras_clave": "lista de palabras clave",
                   "concordancia": "amplia | frase | exacta (por defecto frase)"}
PARAMS_NEGATIVAS = {"campana_id": "ID de la campaña", "palabras_clave": "lista de palabras clave negativas",
                    "concordancia": "amplia | frase | exacta (por defecto frase)"}


async def prev_palabras(ctx: Contexto, p: dict) -> dict:
    exigir(p, "grupo_id", "palabras_clave")
    gid = solo_digitos(p["grupo_id"], "grupo_id")
    claves = palabras(p["palabras_clave"], opcion(p.get("concordancia") or "frase", CONCORDANCIAS, "concordancia"))
    grupo = await gaql(ctx.http, ctx.ajustes, ctx.datos["access_token"], ctx.cuenta_id,
                       f"SELECT ad_group.id, ad_group.name, campaign.name FROM ad_group WHERE ad_group.id = {gid}",
                       ctx.extra.get("login_customer_id"))
    if not grupo:
        raise ErrorUsuario("No existe ese grupo de anuncios en esta cuenta")
    ops = [{"adGroupCriterionOperation": {"create": {"adGroup": f"customers/{ctx.cuenta_id}/adGroups/{gid}",
                                                     "status": "ENABLED", "keyword": k}}} for k in claves]
    await _mutate(ctx, ops, validar=True)
    return {"resumen": f"Google Ads · '{grupo[0]['campaign'].get('name')}' › '{grupo[0]['adGroup'].get('name')}': "
                       f"añadir {len(claves)} palabras clave",
            "palabras_clave": [f"{k['text']} ({k['matchType'].lower()})" for k in claves], "_params": {"ops": ops}}


async def prev_negativas(ctx: Contexto, p: dict) -> dict:
    exigir(p, "campana_id", "palabras_clave")
    cid = solo_digitos(p["campana_id"], "campana_id")
    claves = palabras(p["palabras_clave"], opcion(p.get("concordancia") or "frase", CONCORDANCIAS, "concordancia"))
    camp = await gaql(ctx.http, ctx.ajustes, ctx.datos["access_token"], ctx.cuenta_id,
                      f"SELECT campaign.id, campaign.name FROM campaign WHERE campaign.id = {cid}",
                      ctx.extra.get("login_customer_id"))
    if not camp:
        raise ErrorUsuario("No existe esa campaña en esta cuenta")
    ops = [{"campaignCriterionOperation": {"create": {"campaign": f"customers/{ctx.cuenta_id}/campaigns/{cid}",
                                                      "negative": True, "keyword": k}}} for k in claves]
    await _mutate(ctx, ops, validar=True)
    return {"resumen": f"Google Ads · '{camp[0]['campaign'].get('name')}': añadir {len(claves)} negativas",
            "negativas": [f"{k['text']} ({k['matchType'].lower()})" for k in claves], "_params": {"ops": ops}}


async def prev_declarar_ue(ctx: Contexto, p: dict) -> dict:
    pendientes = await gaql(ctx.http, ctx.ajustes, ctx.datos["access_token"], ctx.cuenta_id,
                            "SELECT campaign.id, campaign.name FROM campaign "
                            "WHERE campaign.missing_eu_political_advertising_declaration = TRUE",
                            ctx.extra.get("login_customer_id"))
    if not pendientes:
        raise ErrorUsuario("Todas las campañas de la cuenta ya tienen la declaración de publicidad política de la UE")
    ops = [{"campaignOperation": {"update": {
        "resourceName": f"customers/{ctx.cuenta_id}/campaigns/{f['campaign']['id']}",
        "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"},
        "updateMask": "containsEuPoliticalAdvertising"}} for f in pendientes]
    return {"resumen": f"Google Ads · declarar {len(pendientes)} campañas como SIN publicidad política de la UE",
            "campanas": [f["campaign"].get("name") for f in pendientes],
            "avisos": ["Sin esta declaración, Google bloquea cualquier cambio de campañas en la cuenta.",
                       "Confírmalo solo si ninguna de estas campañas contiene publicidad política."],
            "_params": {"ops": ops}}


async def ejec_ops(ctx: Contexto, p: dict) -> dict:
    res = await _mutate(ctx, p["ops"], validar=False)
    return {"ok": True, "operaciones": len(res.get("mutateOperationResponses", []))}
