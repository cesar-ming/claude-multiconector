"""WordPress: REST API (wp-json/wp/v2) con contraseña de aplicación."""
from __future__ import annotations

import base64
from collections import Counter
from datetime import date, datetime, timedelta

from ..http import ErrorUsuario, json_o_error, peticion
from .base import Accion, Contexto, Login, Plataforma, exigir, opcion, solo_digitos


def _sitio(valor: str) -> str:
    s = (valor or "").strip().rstrip("/")
    if not s.startswith("https://"):
        raise ErrorUsuario("La URL del sitio debe empezar por https:// (las contraseñas de aplicación exigen HTTPS)")
    return s


def _cab(datos: dict) -> dict:
    par = f"{datos['usuario']}:{datos['contrasena_aplicacion'].replace(' ', '')}".encode()
    return {"Authorization": "Basic " + base64.b64encode(par).decode()}


async def wp(ctx: Contexto, metodo: str, ruta: str, **kw):
    r = await peticion(ctx.http, "wordpress", metodo, f"{ctx.datos['sitio']}/wp-json/wp/v2/{ruta}",
                       intentos=1 if metodo != "GET" else 3, headers=_cab(ctx.datos), **kw)
    return json_o_error(r, "wordpress"), r


class LoginWordPress(Login):
    id = "wordpress"
    nombre = "WordPress"
    plataformas = ("wordpress",)
    oauth = False
    campos_manuales = {"sitio": "https://www.ejemplo.com",
                       "usuario": "usuario de WordPress con rol Editor o Administrador",
                       "contrasena_aplicacion": "Usuarios → Perfil → Contraseñas de aplicación"}

    async def preparar_manual(self, http, a, datos):
        limpio = await super().preparar_manual(http, a, datos)
        limpio["sitio"] = _sitio(limpio["sitio"])
        ctx = Contexto(http, a, limpio)
        yo, _ = await wp(ctx, "GET", "users/me", params={"context": "edit"})
        if not set(yo.get("roles", [])) & {"administrator", "editor", "author"}:
            raise ErrorUsuario("Ese usuario no puede publicar entradas (necesita rol Autor, Editor o Administrador)")
        return {**limpio, "nombre_usuario": yo.get("name")}

    async def listar_cuentas(self, http, a, datos):
        r = await peticion(http, "wordpress", "GET", f"{datos['sitio']}/wp-json/", headers=_cab(datos))
        info = json_o_error(r, "wordpress")
        return [{"plataforma": "wordpress", "cuenta_id": datos["sitio"], "nombre": info.get("name"), "extra": {}}]


ESTADOS_WP = {"borrador": "draft", "draft": "draft", "publicar": "publish", "publicado": "publish",
              "publish": "publish", "programar": "future", "programado": "future", "future": "future",
              "pendiente": "pending", "pending": "pending", "privado": "private", "private": "private"}


class WordPress(Plataforma):
    id = "wordpress"
    nombre = "WordPress"
    tipo = "cms"
    login = "wordpress"

    async def metricas(self, ctx, desde: date, hasta: date, opciones):
        conteo: Counter = Counter()
        pagina = 1
        while True:
            entradas, r = await wp(ctx, "GET", "posts", params={
                "after": f"{desde.isoformat()}T00:00:00", "before": f"{(hasta + timedelta(days=1)).isoformat()}T00:00:00",
                "per_page": 100, "page": pagina, "_fields": "id,date", "status": "publish"})
            for e in entradas:
                conteo[e["date"][:10]] += 1
            if pagina >= int(r.headers.get("x-wp-totalpages", 1)):
                break
            pagina += 1
        filas = [{"fecha": d, "plataforma": "wordpress", "cuenta_id": ctx.cuenta_id, "metrica": "entradas_publicadas",
                  "valor": n} for d, n in sorted(conteo.items())]
        return {"filas": filas, "notas": ["WordPress no guarda tráfico: para visitas usa GA4 o Search Console."]}

    async def contenido(self, ctx, opciones):
        entradas, _ = await wp(ctx, "GET", "posts", params={
            "per_page": int(opciones.get("limite", 20)), "status": "publish,draft,future,pending",
            "_fields": "id,title,status,date,link", "context": "edit"})
        return {"tipo": "entradas", "elementos": [
            {"id": e["id"], "titulo": (e.get("title") or {}).get("raw") or (e.get("title") or {}).get("rendered"),
             "estado": e.get("status"), "fecha": e.get("date"), "enlace": e.get("link")} for e in entradas]}

    def acciones(self):
        return {
            "publicar_entrada": Accion(
                "publicar_entrada", "publicar", "Crea una entrada. Por defecto queda en borrador.",
                {"titulo": "título", "contenido_html": "cuerpo en HTML", "extracto": "resumen (opcional)",
                 "estado": "borrador | publicar | programar (por defecto borrador)",
                 "fecha": "fecha local ISO para programar, p. ej. 2026-11-20T09:00:00 (opcional)",
                 "slug": "URL amigable (opcional)"}, self._prev_crear, self._ejec_crear),
            "actualizar_entrada": Accion(
                "actualizar_entrada", "publicar", "Modifica una entrada existente.",
                {"entrada_id": "ID de la entrada", "titulo": "(opcional)", "contenido_html": "(opcional)",
                 "extracto": "(opcional)", "estado": "(opcional)"}, self._prev_actualizar, self._ejec_actualizar),
        }

    def _campos(self, p: dict) -> dict:
        campos = {}
        if p.get("titulo"):
            campos["title"] = p["titulo"]
        if p.get("contenido_html"):
            campos["content"] = p["contenido_html"]
        if p.get("extracto"):
            campos["excerpt"] = p["extracto"]
        if p.get("slug"):
            campos["slug"] = p["slug"]
        if p.get("estado"):
            campos["status"] = opcion(p["estado"], ESTADOS_WP, "estado")
        if p.get("fecha"):
            try:
                datetime.fromisoformat(p["fecha"])
            except ValueError:
                raise ErrorUsuario("fecha debe ser ISO, p. ej. 2026-11-20T09:00:00") from None
            campos["date"] = p["fecha"]
        if campos.get("status") == "future" and not campos.get("date"):
            raise ErrorUsuario("Para programar indica la fecha")
        return campos

    async def _prev_crear(self, ctx, p):
        exigir(p, "titulo", "contenido_html")
        campos = {"status": "draft", **self._campos(p)}
        avisos = ["Se publicará y será visible en la web."] if campos["status"] == "publish" else []
        return {"resumen": f"WordPress · {ctx.cuenta_id}: nueva entrada '{p['titulo']}' ({campos['status']})",
                "contenido": {"titulo": p["titulo"], "estado": campos["status"], "fecha": campos.get("date"),
                              "longitud_html": len(p["contenido_html"])}, "avisos": avisos, "_params": campos}

    async def _ejec_crear(self, ctx, p):
        e, _ = await wp(ctx, "POST", "posts", json=p)
        return {"ok": True, "id": e.get("id"), "estado": e.get("status"), "enlace": e.get("link")}

    async def _prev_actualizar(self, ctx, p):
        exigir(p, "entrada_id")
        eid = solo_digitos(p["entrada_id"], "entrada_id")
        campos = self._campos(p)
        if not campos:
            raise ErrorUsuario("No hay nada que cambiar")
        actual, _ = await wp(ctx, "GET", f"posts/{eid}", params={"context": "edit", "_fields": "id,title,status"})
        avisos = ["La entrada pasará a estar publicada."] if campos.get("status") == "publish" else []
        return {"resumen": f"WordPress · entrada {eid} '{(actual.get('title') or {}).get('raw')}' "
                           f"({actual.get('status')}): cambiar {', '.join(campos)}",
                "avisos": avisos, "_params": {"id": eid, "campos": campos}}

    async def _ejec_actualizar(self, ctx, p):
        e, _ = await wp(ctx, "POST", f"posts/{p['id']}", json=p["campos"])
        return {"ok": True, "id": e.get("id"), "estado": e.get("status"), "enlace": e.get("link")}
