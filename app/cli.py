"""Utilidades de línea de comandos.

    python -m app.cli generar-clave     # clave Fernet para CLAVE_CIFRADO
    python -m app.cli generar-secretos  # API_KEY, MCP_TOKEN y CLAVE_CIFRADO nuevos
    python -m app.cli comprobar         # muestra qué apps están configuradas
"""
import secrets
import sys

from cryptography.fernet import Fernet


def main() -> None:
    orden = sys.argv[1] if len(sys.argv) > 1 else ""
    if orden == "generar-clave":
        print(Fernet.generate_key().decode())
    elif orden == "generar-secretos":
        print(f"API_KEY={secrets.token_urlsafe(32)}")
        print(f"MCP_TOKEN={secrets.token_urlsafe(32)}")
        print(f"CLAVE_CIFRADO={Fernet.generate_key().decode()}")
    elif orden == "comprobar":
        from .config import get_settings
        from .proveedores.registro import LOGINS
        a = get_settings()
        print(f"URL pública: {a.base_url}")
        for faltan, nombre in (("api_key", "API_KEY"), ("mcp_token", "MCP_TOKEN"), ("clave_cifrado", "CLAVE_CIFRADO")):
            print(f"  {'OK ' if getattr(a, faltan) else 'FALTA'} {nombre}")
        print(f"  Publicar: {'sí' if a.permitir_publicar else 'no'} · Gestionar: {'sí' if a.permitir_gestionar else 'no'}")
        for l in LOGINS.values():
            estado = "manual" if not l.oauth else ("OK" if l.configurado(a) else "sin configurar")
            uri = f"  → {a.base_url.rstrip('/')}/oauth/callback/{l.id}" if l.oauth else ""
            print(f"  [{estado:>14}] {l.nombre}{uri}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
