"""Creación de campañas (siempre en pausa) y optimización."""
import json
from urllib.parse import parse_qs

import httpx
import pytest

from app.http import ErrorPlataforma, ErrorUsuario
from app.proveedores.google_campanas import palabras
from tests.conftest import cuerpo
from tests.test_plataformas import conectar_meta

pytestmark = pytest.mark.asyncio

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100

BASE_META = {"nombre": "Navidad · Guantes", "objetivo": "ventas", "presupuesto_diario": 40,
             "url": "https://echalekuboxeo.com/guantes", "texto_principal": "Guantes para entrenar en serio",
             "titular": "Guantes Echaleku", "imagen_url": "https://cdn.echaleku.com/guantes.png"}


def form(req: httpx.Request) -> dict:
    return {k: v[0] for k, v in parse_qs(req.content.decode()).items()}


async def meta_lista(s, sim):
    cred = await conectar_meta(s, sim)
    con = await s.vincular(cred, "echaleku", "meta_ads", "act_1", "test")
    sim.en("GET", r"/act_1\?", {"currency": "EUR", "name": "Echaleku"})
    sim.en("GET", r"act_1/promote_pages", {"data": [{"id": "p1", "name": "Echaleku Boxeo"}]})
    sim.en("GET", r"act_1/adspixels", {"data": [{"id": "px9", "name": "Píxel web"}]})
    return con


async def test_meta_vista_previa_valida_sin_crear(crear_servicio, sim):
    s = crear_servicio()
    con = await meta_lista(s, sim)
    sim.en("POST", r"act_1/campaigns$", lambda r: {"success": True} if "validate_only" in r.content.decode()
           else httpx.Response(500, json={"error": {"message": "no debería crearse"}}))
    p = await s.proponer(con["id"], "lanzar_campana", BASE_META, "test")
    assert "EN PAUSA" in p["resumen"]
    assert p["estructura"]["conjunto"]["pixel"] == "Píxel web"
    assert p["estructura"]["anuncio"]["boton"] == "SHOP_NOW"
    posts = [r for r in sim.llamadas if r.method == "POST"]
    assert len(posts) == 1 and form(posts[0])["execution_options"] == '["validate_only"]'
    assert form(posts[0])["daily_budget"] == "4000" and form(posts[0])["status"] == "PAUSED"


async def test_meta_topes_y_validaciones(crear_servicio, sim):
    s = crear_servicio(max_presupuesto_diario=30)
    con = await meta_lista(s, sim)
    with pytest.raises(ErrorUsuario, match="tope"):
        sim.en("POST", r"act_1/campaigns$", {"success": True})
        await s.proponer(con["id"], "lanzar_campana", BASE_META, "test")
    with pytest.raises(ErrorUsuario, match="entre 18 y 25"):
        await s.proponer(con["id"], "lanzar_campana", {**BASE_META, "presupuesto_diario": 10, "edad_min": 30}, "test")
    with pytest.raises(ErrorUsuario, match="https"):
        await s.proponer(con["id"], "lanzar_campana", {**BASE_META, "presupuesto_diario": 10,
                                                       "url": "http://echaleku.com"}, "test")


async def test_meta_lanza_todo_en_pausa(crear_servicio, sim):
    s = crear_servicio()
    con = await meta_lista(s, sim)
    sim.en("POST", r"act_1/campaigns$", lambda r: {"success": True} if "validate_only" in r.content.decode()
           else {"id": "c100"})
    p = await s.proponer(con["id"], "lanzar_campana", {**BASE_META, "publico_advantage": False,
                                                       "edad_min": 25, "edad_max": 45}, "test")
    sim.en("GET", r"cdn.echaleku.com/guantes.png", httpx.Response(200, content=PNG, headers={"content-type": "image/png"}))
    sim.en("POST", r"act_1/adimages$", {"images": {"bytes": {"hash": "h123"}}})
    sim.en("POST", r"act_1/adsets$", {"id": "s200"})
    sim.en("POST", r"act_1/adcreatives$", {"id": "cr300"})
    sim.en("POST", r"act_1/ads$", {"id": "a400"})
    r = await s.confirmar(p["token"], "test")
    assert r["resultado"]["estado"] == "EN PAUSA"
    assert (r["resultado"]["campana"], r["resultado"]["anuncio"]) == ("c100", "a400")
    conjunto = form(sim.ultima(r"act_1/adsets$"))
    assert conjunto["status"] == "PAUSED" and conjunto["optimization_goal"] == "OFFSITE_CONVERSIONS"
    assert json.loads(conjunto["promoted_object"]) == {"pixel_id": "px9", "custom_event_type": "PURCHASE"}
    assert json.loads(conjunto["targeting"]) == {"geo_locations": {"countries": ["ES"]}, "age_min": 25,
                                                 "targeting_automation": {"advantage_audience": 0}, "age_max": 45}
    creatividad = form(sim.ultima(r"act_1/adcreatives$"))
    assert json.loads(creatividad["degrees_of_freedom_spec"])["creative_features_spec"]["product_extensions"][
        "enroll_status"] == "OPT_OUT"
    historia = json.loads(creatividad["object_story_spec"])
    assert historia["page_id"] == "p1" and historia["link_data"]["image_hash"] == "h123"
    assert form(sim.ultima(r"act_1/ads$"))["status"] == "PAUSED"


async def test_meta_limpia_si_falla_a_mitad(crear_servicio, sim):
    s = crear_servicio()
    con = await meta_lista(s, sim)
    sim.en("POST", r"act_1/campaigns$", lambda r: {"success": True} if "validate_only" in r.content.decode()
           else {"id": "c100"})
    p = await s.proponer(con["id"], "lanzar_campana", BASE_META, "test")
    sim.en("GET", r"cdn.echaleku.com/guantes.png", httpx.Response(200, content=PNG, headers={"content-type": "image/png"}))
    sim.en("POST", r"act_1/adimages$", {"images": {"x": {"hash": "h1"}}})
    sim.en("POST", r"act_1/adsets$", httpx.Response(400, json={"error": {"message": "Invalid targeting"}}))
    sim.en("POST", r"/c100$", {"success": True})
    with pytest.raises(ErrorPlataforma, match="eliminada para no dejar restos"):
        await s.confirmar(p["token"], "test")
    assert form(sim.ultima(r"/c100$"))["status"] == "DELETED"
    assert s.db.accion(p["token"])["estado"] == "fallida"


# ---------------------------------------------------------------- Google Ads

BASE_GOOGLE = {"nombre": "Búsqueda · Guantes", "presupuesto_diario": 25,
               "url_final": "https://echalekuboxeo.com/guantes",
               "palabras_clave": ["guantes de boxeo", "[guantes boxeo 14 oz]", '"comprar guantes de boxeo"'],
               "titulares": ["Guantes de boxeo", "Envío en 24-48 h", "Echaleku Boxeo"],
               "descripciones": ["Guantes para entrenar y competir.", "Elige tus onzas y recíbelos en casa."],
               "negativas": ["gratis", "segunda mano"], "ruta1": "guantes"}


async def google_lista(s, sim):
    cred = s.db.guardar_credencial("google", {"access_token": "g", "refresh_token": "r"})
    sim.en("GET", r"customers:listAccessibleCustomers", {"resourceNames": ["customers/123"]})
    sim.en("POST", r"customers/123/googleAds:search", {"results": [{"customer": {
        "id": "123", "descriptiveName": "Echaleku", "currencyCode": "EUR", "manager": False}}]})
    return await s.vincular(cred, "echaleku", "google_ads", "123", "test")


async def test_palabras_clave_concordancias():
    assert palabras(["guantes", "[guantes 14 oz]", '"comprar guantes"', "guantes"], "PHRASE") == [
        {"text": "guantes", "matchType": "PHRASE"}, {"text": "guantes 14 oz", "matchType": "EXACT"},
        {"text": "comprar guantes", "matchType": "PHRASE"}]


async def test_google_busqueda_validada_y_atomica(crear_servicio, sim):
    s = crear_servicio()
    con = await google_lista(s, sim)

    def mutate(req):
        b = cuerpo(req)
        if b["validateOnly"]:
            return {}
        return {"mutateOperationResponses": [{"campaignBudgetResult": {}},
                                             {"campaignResult": {"resourceName": "customers/123/campaigns/555"}},
                                             {"adGroupResult": {"resourceName": "customers/123/adGroups/777"}}]}
    sim.en("POST", r"customers/123/googleAds:mutate$", mutate)
    p = await s.proponer(con["id"], "lanzar_campana_busqueda", BASE_GOOGLE, "test")
    validacion = cuerpo(sim.ultima("googleAds:mutate"))
    assert validacion["validateOnly"] is True and validacion["partialFailure"] is False
    ops = validacion["mutateOperations"]
    camp = ops[1]["campaignOperation"]["create"]
    assert camp["status"] == "PAUSED" and camp["advertisingChannelType"] == "SEARCH"
    assert camp["containsEuPoliticalAdvertising"] == "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"
    assert camp["campaignBudget"] == "customers/123/campaignBudgets/-1" and "maximizeConversions" in camp
    assert ops[0]["campaignBudgetOperation"]["create"]["amountMicros"] == "25000000"
    criterios = [o["campaignCriterionOperation"]["create"] for o in ops if "campaignCriterionOperation" in o]
    assert {"geoTargetConstant": "geoTargetConstants/2724"} in [c.get("location") for c in criterios]
    assert sum(1 for c in criterios if c.get("negative")) == 2
    anuncio = [o for o in ops if "adGroupAdOperation" in o][0]["adGroupAdOperation"]["create"]["ad"]
    assert len(anuncio["responsiveSearchAd"]["headlines"]) == 3 and anuncio["responsiveSearchAd"]["path1"] == "guantes"
    r = await s.confirmar(p["token"], "test")
    assert r["resultado"] | {} == r["resultado"] and r["resultado"]["campana_id"] == "555"
    assert r["resultado"]["grupo_id"] == "777" and r["resultado"]["estado"] == "EN PAUSA"
    assert cuerpo(sim.ultima("googleAds:mutate"))["validateOnly"] is False


async def test_google_textos_largos_rechazados(crear_servicio, sim):
    s = crear_servicio()
    con = await google_lista(s, sim)
    largo = {**BASE_GOOGLE, "titulares": ["Guantes de boxeo profesionales de piel", "Envío rápido", "Echaleku"]}
    with pytest.raises(ErrorUsuario, match="superan 30"):
        await s.proponer(con["id"], "lanzar_campana_busqueda", largo, "test")
    with pytest.raises(ErrorUsuario, match="entre 2 y 4 descripciones"):
        await s.proponer(con["id"], "lanzar_campana_busqueda", {**BASE_GOOGLE, "descripciones": ["Solo una."]}, "test")


async def test_google_error_de_validacion_se_muestra(crear_servicio, sim):
    s = crear_servicio()
    con = await google_lista(s, sim)
    sim.en("POST", r"googleAds:mutate$", httpx.Response(400, json={"error": {
        "message": "Request contains an invalid argument.", "details": [{"errors": [{"message": "Too short."}]}]}}))
    with pytest.raises(ErrorPlataforma, match="invalid argument"):
        await s.proponer(con["id"], "lanzar_campana_busqueda", BASE_GOOGLE, "test")
    assert not s.db.listar_acciones("pendiente")


async def test_google_negativas_y_terminos(crear_servicio, sim):
    s = crear_servicio()
    con = await google_lista(s, sim)
    sim.en("POST", r"customers/123/googleAds:search", lambda r: {"results": [
        {"campaign": {"id": "555", "name": "Búsqueda · Guantes"}, "adGroup": {"id": "777"},
         "searchTermView": {"searchTerm": "guantes boxeo gratis", "status": "NONE"},
         "metrics": {"impressions": "40", "clicks": "6", "costMicros": "4200000", "conversions": 0}}]}
        if "search_term_view" in cuerpo(r)["query"] else {"results": [{"campaign": {"id": "555", "name": "Búsqueda · Guantes"}}]})
    terminos = await s.contenido(con["id"], {"tipo": "terminos_busqueda", "campana_id": "555"})
    assert terminos["elementos"][0] == {"campana_id": "555", "campana": "Búsqueda · Guantes", "grupo_id": "777",
                                        "termino": "guantes boxeo gratis", "estado": "NONE", "impresiones": 40,
                                        "clics": 6, "coste": 4.2, "conversiones": 0.0}
    assert "campaign.id = 555" in cuerpo(sim.ultima("googleAds:search"))["query"]
    sim.en("POST", r"googleAds:mutate$", lambda r: {} if cuerpo(r)["validateOnly"] else
           {"mutateOperationResponses": [{"campaignCriterionResult": {}}]})
    p = await s.proponer(con["id"], "anadir_negativas", {"campana_id": "555", "palabras_clave": ["gratis"]}, "test")
    r = await s.confirmar(p["token"], "test")
    op = cuerpo(sim.ultima("googleAds:mutate"))["mutateOperations"][0]["campaignCriterionOperation"]["create"]
    assert op == {"campaign": "customers/123/campaigns/555", "negative": True,
                  "keyword": {"text": "gratis", "matchType": "PHRASE"}} and r["resultado"]["operaciones"] == 1


async def test_google_declarar_ue(crear_servicio, sim):
    s = crear_servicio()
    con = await google_lista(s, sim)
    sim.en("POST", r"customers/123/googleAds:search", {"results": [{"campaign": {"id": "9", "name": "Antigua"}}]})
    p = await s.proponer(con["id"], "declarar_sin_publicidad_politica", {}, "test")
    assert p["campanas"] == ["Antigua"]
    sim.en("POST", r"googleAds:mutate$", {"mutateOperationResponses": [{"campaignResult": {}}]})
    await s.confirmar(p["token"], "test")
    op = cuerpo(sim.ultima("googleAds:mutate"))["mutateOperations"][0]["campaignOperation"]
    assert op["updateMask"] == "containsEuPoliticalAdvertising"
