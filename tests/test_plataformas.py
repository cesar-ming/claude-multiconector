import json
from datetime import date
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.http import ErrorPlataforma, ErrorUsuario
from app.proveedores.shopify import hmac_valido
from tests.conftest import cuerpo

pytestmark = pytest.mark.asyncio


async def conectar_meta(s, sim):
    url = s.enlace_oauth("meta")
    state = parse_qs(urlparse(url).query)["state"][0]
    sim.en("GET", r"oauth/access_token\?.*code=abc", {"access_token": "corto"})
    sim.en("GET", r"oauth/access_token\?.*fb_exchange_token", {"access_token": "largo", "expires_in": 5184000})
    sim.en("GET", r"/me/adaccounts", {"data": [{"id": "act_1", "name": "Echaleku", "currency": "EUR"}]})
    sim.en("GET", r"/me/accounts", {"data": [{"id": "p1", "name": "Echaleku Page",
                                              "instagram_business_account": {"id": "ig1", "username": "echaleku"}}]})
    cred = await s.completar_oauth("meta", {"state": state, "code": "abc"})
    return cred


async def test_oauth_meta_y_cuentas(crear_servicio, sim):
    s = crear_servicio()
    cred = await conectar_meta(s, sim)
    guardada = s.db.credencial(cred)
    assert guardada["datos"]["access_token"] == "largo" and guardada["caduca"]
    cuentas = await s.cuentas_disponibles(cred)
    assert {(c["plataforma"], c["cuenta_id"]) for c in cuentas} == {
        ("meta_ads", "act_1"), ("facebook", "p1"), ("instagram", "ig1")}
    # el state es de un solo uso
    with pytest.raises(ErrorUsuario):
        await s.completar_oauth("meta", {"state": "otro", "code": "abc"})


async def test_tokens_cifrados_en_disco(crear_servicio, sim):
    s = crear_servicio()
    cred = await conectar_meta(s, sim)
    crudo = s.db._q("SELECT datos FROM credenciales WHERE id=?", (cred,))[0]["datos"]
    assert b"largo" not in crudo


async def test_meta_metricas_sin_doble_conteo(crear_servicio, sim):
    s = crear_servicio()
    cred = await conectar_meta(s, sim)
    con = await s.vincular(cred, "Echaleku Boxeo", "meta_ads", "act_1", "test")
    assert con["cliente"] == "echaleku-boxeo"
    sim.en("GET", r"act_1/insights", {"data": [{
        "date_start": "2026-11-01", "campaign_id": "c1", "campaign_name": "Prospección", "spend": "100.50",
        "impressions": "10000", "clicks": "250", "account_currency": "EUR",
        "actions": [{"action_type": "omni_purchase", "value": "5"}, {"action_type": "purchase", "value": "5"},
                    {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "5"}],
        "action_values": [{"action_type": "omni_purchase", "value": "450"}, {"action_type": "purchase", "value": "450"}]}]})
    res = await s.metricas(con["id"], "2026-11-01", "2026-11-01")
    f = res["filas"][0]
    assert f["inversion"] == 100.5 and f["conversiones"] == 5 and f["ingresos"] == 450 and f["moneda"] == "EUR"
    assert "omni_purchase" in res["notas"][0]


async def test_presupuesto_con_vista_previa_limites_y_confirmacion(crear_servicio, sim):
    s = crear_servicio(max_presupuesto_diario=300, max_subida_presupuesto_pct=50)
    cred = await conectar_meta(s, sim)
    con = await s.vincular(cred, "echaleku", "meta_ads", "act_1", "test")
    sim.en("GET", r"/123\?", {"id": "123", "name": "Prospección", "daily_budget": "10000", "account_id": "1"})
    # subida del 100 %: bloqueada
    with pytest.raises(ErrorUsuario, match="supera el 50"):
        await s.proponer(con["id"], "cambiar_presupuesto_diario", {"objeto_id": "123", "importe": 200}, "test")
    # por encima del tope absoluto: bloqueada
    sim.en("GET", r"/123\?", {"id": "123", "name": "Prospección", "daily_budget": "25000", "account_id": "1"})
    with pytest.raises(ErrorUsuario, match="tope"):
        await s.proponer(con["id"], "cambiar_presupuesto_diario", {"objeto_id": "123", "importe": 320}, "test")
    # válida: propuesta sin ejecutar
    sim.en("GET", r"/123\?", {"id": "123", "name": "Prospección", "daily_budget": "10000", "account_id": "1"})
    p = await s.proponer(con["id"], "cambiar_presupuesto_diario", {"objeto_id": "123", "importe": 140}, "test")
    assert p["cambio_presupuesto"] == {"actual": 100.0, "nuevo": 140.0, "moneda": "EUR"}
    assert not [r for r in sim.llamadas if r.method == "POST"]
    sim.en("POST", r"/123$", {"success": True})
    r = await s.confirmar(p["token"], "test")
    assert r["ok"]
    enviado = parse_qs(sim.ultima(r"/123$").content.decode())
    assert enviado["daily_budget"] == ["14000"]
    with pytest.raises(ErrorUsuario, match="ya está"):
        await s.confirmar(p["token"], "test")


async def test_objeto_de_otra_cuenta_rechazado(crear_servicio, sim):
    s = crear_servicio()
    cred = await conectar_meta(s, sim)
    con = await s.vincular(cred, "echaleku", "meta_ads", "act_1", "test")
    sim.en("GET", r"/999\?", {"id": "999", "name": "Ajena", "status": "ACTIVE", "account_id": "777"})
    with pytest.raises(ErrorUsuario, match="no pertenece"):
        await s.proponer(con["id"], "cambiar_estado", {"objeto_id": "999", "estado": "pausar"}, "test")


async def test_permisos_desactivados(crear_servicio, sim):
    s = crear_servicio(permitir_gestionar=False)
    cred = await conectar_meta(s, sim)
    con = await s.vincular(cred, "echaleku", "meta_ads", "act_1", "test")
    with pytest.raises(ErrorUsuario, match="desactivado"):
        await s.proponer(con["id"], "cambiar_estado", {"objeto_id": "1", "estado": "pausar"}, "test")


async def test_google_ads_metricas_y_presupuesto(crear_servicio, sim):
    s = crear_servicio()
    cred = s.db.guardar_credencial("google", {"access_token": "g", "refresh_token": "r", "caduca": "2000-01-01T00:00:00+00:00"})
    sim.en("POST", r"oauth2.googleapis.com/token", {"access_token": "g2", "expires_in": 3599})
    sim.en("GET", r"customers:listAccessibleCustomers", {"resourceNames": ["customers/1234567890"]})
    sim.en("POST", r"customers/1234567890/googleAds:search",
           lambda req: {"results": [{"customer": {"id": "1234567890", "descriptiveName": "Echaleku",
                                                  "currencyCode": "EUR", "manager": False}}]})
    con = await s.vincular(cred, "echaleku", "google_ads", "1234567890", "test")
    assert s.db.credencial(cred)["datos"]["access_token"] == "g2"  # refrescado

    paginas = iter([
        {"results": [{"segments": {"date": "2026-11-01"}, "customer": {"currencyCode": "EUR"},
                      "campaign": {"id": "11", "name": "Shopping"},
                      "metrics": {"costMicros": "25500000", "impressions": "900", "clicks": "40",
                                  "conversions": 3.0, "conversionsValue": 180.0}}], "nextPageToken": "p2"},
        {"results": [{"segments": {"date": "2026-11-02"}, "customer": {"currencyCode": "EUR"},
                      "campaign": {"id": "11", "name": "Shopping"},
                      "metrics": {"costMicros": "1000000", "impressions": "10", "clicks": "1"}}]}])
    sim.en("POST", r"googleAds:search", lambda req: next(paginas))
    res = await s.metricas(con["id"], "2026-11-01", "2026-11-02")
    assert [f["inversion"] for f in res["filas"]] == [25.5, 1.0]
    assert res["filas"][0]["ingresos"] == 180.0
    assert "BETWEEN '2026-11-01' AND '2026-11-02'" in cuerpo(sim.ultima("googleAds:search"))["query"]

    sim.en("POST", r"googleAds:search", {"results": [{"campaign": {"id": "11", "name": "Shopping", "status": "ENABLED"},
                                                      "campaignBudget": {"resourceName": "customers/1234567890/campaignBudgets/9",
                                                                         "amountMicros": "50000000", "explicitlyShared": True},
                                                      "customer": {"currencyCode": "EUR"}}]})
    p = await s.proponer(con["id"], "cambiar_presupuesto_diario", {"campana_id": "11", "importe": 60}, "test")
    assert any("COMPARTIDO" in a for a in p["avisos"])
    sim.en("POST", r"campaignBudgets:mutate", {"results": [{}]})
    await s.confirmar(p["token"], "test")
    op = cuerpo(sim.ultima("campaignBudgets:mutate"))["operations"][0]
    assert op["update"]["amountMicros"] == "60000000" and op["updateMask"] == "amountMicros"
    with pytest.raises(ErrorUsuario, match="numérico"):
        await s.proponer(con["id"], "cambiar_estado", {"campana_id": "1 OR 1=1", "estado": "pausar"}, "test")


async def test_linkedin_ads_restli(crear_servicio, sim):
    s = crear_servicio()
    cred = s.db.guardar_credencial("linkedin_ads", {"access_token": "l"})
    sim.en("GET", r"adAccountUsers", {"elements": [{"account": "urn:li:sponsoredAccount:55", "role": "ACCOUNT_MANAGER"}]})
    sim.en("GET", r"adAccounts/55$", {"name": "Echaleku B2B", "currency": "EUR"})
    con = await s.vincular(cred, "echaleku", "linkedin_ads", "55", "test")
    sim.en("GET", r"adAccounts/55/adCampaigns\?", {"elements": [{"id": 77, "name": "Clubes"}], "metadata": {}})
    sim.en("GET", r"adAnalytics", {"elements": [{"dateRange": {"start": {"year": 2026, "month": 11, "day": 3}},
                                                 "pivotValues": ["urn:li:sponsoredCampaign:77"],
                                                 "costInLocalCurrency": "12.34", "impressions": 500, "clicks": 9,
                                                 "externalWebsiteConversions": 1}]})
    res = await s.metricas(con["id"], "2026-11-01", "2026-11-05")
    f = res["filas"][0]
    assert (f["fecha"], f["campana"], f["inversion"]) == ("2026-11-03", "Clubes", 12.34)
    req = sim.ultima("adAnalytics")
    assert "List(urn%3Ali%3AsponsoredAccount%3A55)" in str(req.url)
    assert req.headers["Linkedin-Version"] == "202610" and req.headers["X-Restli-Protocol-Version"] == "2.0.0"


async def test_tiktok_ads_error_code_y_tramos(crear_servicio, sim):
    s = crear_servicio()
    cred = s.db.guardar_credencial("tiktok_ads", {"access_token": "t"})
    sim.en("GET", r"oauth2/advertiser/get", {"code": 0, "data": {"list": [{"advertiser_id": 42, "advertiser_name": "Echaleku"}]}})
    sim.en("GET", r"advertiser/info", {"code": 0, "data": {"list": [{"advertiser_id": "42", "currency": "EUR"}]}})
    con = await s.vincular(cred, "echaleku", "tiktok_ads", "42", "test")
    sim.en("GET", r"report/integrated/get", {"code": 0, "data": {"page_info": {"total_page": 1}, "list": [
        {"dimensions": {"campaign_id": "9", "stat_time_day": "2026-10-05 00:00:00"},
         "metrics": {"campaign_name": "Spark", "spend": "20.00", "impressions": "3000", "clicks": "60", "conversion": "2"}}]}})
    res = await s.metricas(con["id"], "2026-09-01", "2026-11-10")
    informes = [r for r in sim.llamadas if "report/integrated" in str(r.url)]
    assert len(informes) == 3  # 71 días -> 3 tramos de 30
    assert res["filas"][0]["ingresos"] is None  # sin métrica de valor configurada: no se inventa
    sim.en("GET", r"report/integrated/get", {"code": 40001, "message": "No permission", "data": {}})
    with pytest.raises(ErrorPlataforma, match="No permission"):
        await s.metricas(con["id"], "2026-11-01", "2026-11-02")


async def test_shopify_hmac_y_ventas_por_dia_local(crear_servicio, sim):
    s = crear_servicio()
    import hashlib, hmac
    params = {"code": "x", "shop": "tienda.myshopify.com", "state": "s", "timestamp": "1"}
    msg = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    params["hmac"] = hmac.new(b"ssec", msg.encode(), hashlib.sha256).hexdigest()
    assert hmac_valido(params, "ssec") and not hmac_valido({**params, "code": "y"}, "ssec")

    cred = s.db.guardar_credencial("shopify", {"tienda": "tienda.myshopify.com", "access_token": "shpat"})

    def gql(req):
        q = cuerpo(req)["query"]
        if "myshopifyDomain" in q:
            return {"data": {"shop": {"name": "Echaleku", "myshopifyDomain": "tienda.myshopify.com",
                                      "currencyCode": "EUR", "ianaTimezone": "Europe/Madrid"}}}
        if "ianaTimezone" in q:
            return {"data": {"shop": {"ianaTimezone": "Europe/Madrid", "currencyCode": "EUR"}}}
        dinero = lambda v: {"shopMoney": {"amount": v, "currencyCode": "EUR"}}
        return {"data": {"orders": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [
            # 23:30 UTC del 1 = 00:30 del 2 en Madrid
            {"createdAt": "2026-11-01T23:30:00Z", "test": False, "cancelledAt": None,
             "currentTotalPriceSet": dinero("121.00"), "currentSubtotalPriceSet": dinero("100.00"),
             "currentTotalTaxSet": dinero("21.00"), "totalRefundedSet": dinero("0.00")},
            {"createdAt": "2026-11-02T10:00:00Z", "test": True, "cancelledAt": None,
             "currentTotalPriceSet": dinero("999"), "currentSubtotalPriceSet": dinero("999"),
             "currentTotalTaxSet": dinero("0"), "totalRefundedSet": dinero("0")}]}}}
    sim.en("POST", r"tienda.myshopify.com/admin/api/2026-10/graphql.json", gql)
    con = await s.vincular(cred, "echaleku", "shopify", "tienda.myshopify.com", "test")
    res = await s.metricas(con["id"], "2026-11-02", "2026-11-02")
    assert res["filas"] == [{"fecha": "2026-11-02", "plataforma": "shopify", "cuenta_id": "tienda.myshopify.com",
                             "pedidos": 1, "cancelados": 0, "ventas_totales": 121.0, "subtotal": 100.0,
                             "impuestos": 21.0, "reembolsado": 0.0, "moneda": "EUR"}]


async def test_wordpress_manual_borrador_por_defecto(crear_servicio, sim):
    s = crear_servicio()
    sim.en("GET", r"ejemplo.com/wp-json/wp/v2/users/me", {"name": "César", "roles": ["editor"]})
    sim.en("GET", r"ejemplo.com/wp-json/$", {"name": "Blog Echaleku"})
    with pytest.raises(ErrorUsuario, match="https"):
        await s.credencial_manual("wordpress", {"sitio": "http://ejemplo.com", "usuario": "c",
                                                "contrasena_aplicacion": "a"}, None, "test")
    cred = await s.credencial_manual("wordpress", {"sitio": "https://ejemplo.com/", "usuario": "cesar",
                                                   "contrasena_aplicacion": "abcd efgh"}, None, "test")
    con = await s.vincular(cred, "echaleku", "wordpress", "https://ejemplo.com", "test")
    p = await s.proponer(con["id"], "publicar_entrada", {"titulo": "Cómo elegir guantes", "contenido_html": "<p>Hola</p>"}, "test")
    assert p["contenido"]["estado"] == "draft"
    sim.en("POST", r"wp/v2/posts$", {"id": 10, "status": "draft", "link": "https://ejemplo.com/?p=10"})
    r = await s.confirmar(p["token"], "test")
    assert r["resultado"]["estado"] == "draft"
    req = sim.ultima(r"wp/v2/posts$")
    assert req.headers["Authorization"].startswith("Basic ") and cuerpo(req)["status"] == "draft"


async def test_resumen_cliente_mer(crear_servicio, sim):
    s = crear_servicio()
    cred_m = await conectar_meta(s, sim)
    await s.vincular(cred_m, "echaleku", "meta_ads", "act_1", "test")
    sim.en("GET", r"act_1/insights", {"data": [{"date_start": "2026-11-01", "campaign_id": "c", "spend": "200",
                                                "impressions": "1", "clicks": "1", "account_currency": "EUR",
                                                "actions": [{"action_type": "purchase", "value": "4"}],
                                                "action_values": [{"action_type": "purchase", "value": "600"}]}]})
    cred_s = s.db.guardar_credencial("shopify", {"tienda": "t.myshopify.com", "access_token": "x"})

    def gql(req):
        q = cuerpo(req)["query"]
        if "myshopifyDomain" in q:
            return {"data": {"shop": {"myshopifyDomain": "t.myshopify.com", "currencyCode": "EUR"}}}
        if "ianaTimezone" in q:
            return {"data": {"shop": {"ianaTimezone": "UTC", "currencyCode": "EUR"}}}
        d = lambda v: {"shopMoney": {"amount": v, "currencyCode": "EUR"}}
        return {"data": {"orders": {"pageInfo": {"hasNextPage": False}, "nodes": [
            {"createdAt": "2026-11-01T10:00:00Z", "test": False, "cancelledAt": None, "currentTotalPriceSet": d("800"),
             "currentSubtotalPriceSet": d("661"), "currentTotalTaxSet": d("139"), "totalRefundedSet": d("0")}]}}}
    sim.en("POST", r"t.myshopify.com/admin", gql)
    await s.vincular(cred_s, "echaleku", "shopify", "t.myshopify.com", "test")
    r = await s.resumen_cliente("echaleku", "2026-11-01", "2026-11-01")
    assert r["por_plataforma"]["meta_ads"]["roas"] == 3.0
    assert r["por_plataforma"]["meta_ads"]["cpa"] == 50.0
    assert r["mer"] == 4.0


async def test_accion_caducada(crear_servicio, sim):
    s = crear_servicio()
    cred = await conectar_meta(s, sim)
    con = await s.vincular(cred, "echaleku", "facebook", "p1", "test")
    sim.en("GET", r"/p1\?", {"name": "Echaleku Page", "access_token": "pt"})
    p = await s.proponer(con["id"], "publicar", {"texto": "Hola"}, "test")
    s.db._q("UPDATE acciones SET caduca='2000-01-01T00:00:00+00:00' WHERE token=?", (p["token"],))
    with pytest.raises(ErrorUsuario, match="caducado"):
        await s.confirmar(p["token"], "test")


async def test_auditoria_sin_secretos(crear_servicio, sim):
    s = crear_servicio()
    sim.en("GET", r"oauth/access_token", lambda r: httpx.Response(400, json={"error": {
        "message": "Invalid code. url?client_secret=msec&access_token=zzz"}}))
    url = s.enlace_oauth("meta")
    state = parse_qs(urlparse(url).query)["state"][0]
    with pytest.raises(ErrorPlataforma):
        await s.completar_oauth("meta", {"state": state, "code": "malo"})
    texto = json.dumps(s.db.listar_auditoria())
    assert "msec" not in texto and "zzz" not in texto


async def test_instagram_publica_carrusel(crear_servicio, sim):
    s = crear_servicio()
    cred = await conectar_meta(s, sim)
    con = await s.vincular(cred, "echaleku", "instagram", "ig1", "test")
    ids = iter(["h1", "h2", "cont"])
    sim.en("POST", r"ig1/media$", lambda r: {"id": next(ids)})
    sim.en("GET", r"/cont\?", {"status_code": "FINISHED"})
    sim.en("POST", r"ig1/media_publish$", {"id": "post99"})
    with pytest.raises(ErrorUsuario, match="entre 2 y 10"):
        await s.proponer(con["id"], "publicar", {"tipo": "carrusel", "media_urls": ["https://x/1.jpg"]}, "test")
    p = await s.proponer(con["id"], "publicar", {"tipo": "carrusel", "texto": "Novedades",
                                                 "media_urls": ["https://x/1.jpg", "https://x/2.jpg"]}, "test")
    r = await s.confirmar(p["token"], "test")
    assert r["resultado"]["id"] == "post99"
    final = parse_qs([q for q in sim.llamadas if q.url.path.endswith("ig1/media")][-1].content.decode())
    assert final["media_type"] == ["CAROUSEL"] and final["children"] == ["h1,h2"]


async def test_facebook_programacion_valida(crear_servicio, sim):
    s = crear_servicio()
    cred = await conectar_meta(s, sim)
    con = await s.vincular(cred, "echaleku", "facebook", "p1", "test")
    sim.en("GET", r"/p1\?", {"name": "Echaleku Page", "access_token": "pt"})
    with pytest.raises(ErrorUsuario, match="zona horaria"):
        await s.proponer(con["id"], "publicar", {"texto": "x", "programar_para": "2030-01-01T10:00:00"}, "test")
    with pytest.raises(ErrorUsuario, match="10 minutos y 30 días"):
        await s.proponer(con["id"], "publicar", {"texto": "x", "programar_para": "2030-01-01T10:00:00+01:00"}, "test")


async def test_linkedin_post_escapa_texto(crear_servicio, sim):
    s = crear_servicio()
    cred = s.db.guardar_credencial("linkedin_organico", {"access_token": "l"})
    sim.en("GET", r"organizationAcls", {"elements": [{"organization": "urn:li:organization:8"}]})
    sim.en("GET", r"organizations/8$", {"localizedName": "Echaleku"})
    con = await s.vincular(cred, "echaleku", "linkedin", "8", "test")
    sim.en("POST", r"rest/posts$", httpx.Response(201, headers={"x-restli-id": "urn:li:share:1"}))
    p = await s.proponer(con["id"], "publicar", {"texto": "Nuevo (guantes) #boxeo"}, "test")
    r = await s.confirmar(p["token"], "test")
    assert r["resultado"]["id"] == "urn:li:share:1"
    enviado = cuerpo(sim.ultima(r"rest/posts$"))
    assert enviado["author"] == "urn:li:organization:8"
    assert enviado["commentary"] == r"Nuevo \(guantes\) \#boxeo"


async def test_tiktok_privacidad_no_permitida(crear_servicio, sim):
    s = crear_servicio()
    cred = s.db.guardar_credencial("tiktok_organico", {"access_token": "t", "open_id": "o1"})
    sim.en("GET", r"open.tiktokapis.com/v2/user/info", {"data": {"user": {"open_id": "o1", "username": "echaleku"}},
                                                        "error": {"code": "ok"}})
    con = await s.vincular(cred, "echaleku", "tiktok", "o1", "test")
    sim.en("POST", r"creator_info/query", {"data": {"creator_username": "echaleku",
                                                    "privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}})
    with pytest.raises(ErrorUsuario, match="no permitida"):
        await s.proponer(con["id"], "publicar_video", {"video_url": "https://v.echaleku.com/a.mp4",
                                                       "privacidad": "publico"}, "test")
    p = await s.proponer(con["id"], "publicar_video", {"video_url": "https://v.echaleku.com/a.mp4"}, "test")
    assert p["contenido"]["privacidad"] == "SELF_ONLY"
