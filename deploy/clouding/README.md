# Servidor MVP en Clouding.io

Servidor `marketificial-mvp` (Clouding.io): 1 vCore, 2 GB de RAM, 20 GB SSD, imagen *Docker (Ubuntu 24.04)*, copias
diarias con 3 días de retención (unos 8,24 €/mes). Firewall `marketificial-web`: solo 22/tcp, 80/tcp, 443/tcp+udp e ICMP.

[`userdata.sh`](userdata.sh) es el script de primer arranque (campo `userData` de la API, máximo 16 000 caracteres):

- instala Docker Compose, fail2ban, actualizaciones automáticas y 2 GB de swap;
- genera **en el servidor** `MCP_OWNER_PASSWORD`, `MCP_TOKEN_SECRET` y `MCP_API_KEY` en `/opt/marketificial/.env`
  y los resume en `/root/marketificial-credenciales.txt` (solo root);
- arranca Caddy con HTTPS automático:
  - `https://mcp.marketificial.com` → multiconector (`/mcp` es el endpoint para Claude);
  - `https://marketificial.com` → página provisional en `/opt/marketificial/site`;
  - `https://www.marketificial.com` → redirige al dominio raíz;
- instala `marketificial-deploy` y un temporizador de systemd que **cada 5 minutos** descarga `main` de este repositorio
  y reconstruye el contenedor si hay cambios (despliegue continuo).

## DNS (zona en Clouding)

| Tipo | Nombre | Valor |
|---|---|---|
| A | `@` | IP pública del servidor |
| A | `www` | IP pública del servidor |
| A | `mcp` | IP pública del servidor |

Caddy obtiene los certificados de Let's Encrypt en cuanto los registros resuelven.

## Operación

```bash
ssh root@marketificial.com
cat /root/marketificial-credenciales.txt              # contraseña OAuth y API key
nano /opt/marketificial/.env                          # credenciales de Google, Meta, LinkedIn, TikTok, Shopify, WordPress
cd /opt/marketificial && docker compose up -d multiconector   # aplicar cambios del .env
docker compose logs -f                                # logs
journalctl -u marketificial-deploy -n 50              # historial de despliegues
tail -n 100 /var/log/marketificial-setup.log          # log del primer arranque
```

Repositorio privado: guarda un token de GitHub de solo lectura (*fine-grained*, permiso *Contents: Read-only* sobre este
repositorio) en `/opt/marketificial/github-token` con `chmod 600`. Si el repositorio es público no hace falta.
