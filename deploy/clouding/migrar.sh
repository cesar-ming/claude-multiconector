#!/bin/bash
# Pasa el servidor marketificial-mvp (Clouding) al conector multicliente en Python.
# Se ejecuta UNA vez como root, después de guardar el token de GitHub en /opt/marketificial/github-token:
#   curl -fsSL -H "Authorization: token $(cat /opt/marketificial/github-token)" \
#     https://raw.githubusercontent.com/cesar-ming/claude-multiconector/main/deploy/clouding/migrar.sh | bash
# Es idempotente: se puede repetir sin perder datos ni cambiar secretos ya creados.
set -euo pipefail
APP=/opt/marketificial
DOMINIO=mcp.marketificial.com
cd $APP
echo "== Marketificial: migración al conector multicliente =="

# 1. Copia de seguridad de la configuración anterior
STAMP=$(date +%Y%m%d-%H%M%S)
cp -a .env ".env.antes-$STAMP" 2>/dev/null || true
cp -a docker-compose.yml "docker-compose.yml.antes-$STAMP" 2>/dev/null || true

# 2. Secretos: se generan aquí y no salen del servidor
touch .env && chmod 600 .env
poner() { grep -q "^$1=" .env || echo "$1=$2" >> .env; }
if ! grep -q "^CLAVE_CIFRADO=" .env; then
  echo "" >> .env
  echo "# --- Conector multicliente (Python) ---" >> .env
fi
poner BASE_URL "https://$DOMINIO"
poner HOSTS_PERMITIDOS "$DOMINIO"
poner API_KEY "$(openssl rand -hex 24)"
poner MCP_TOKEN "$(openssl rand -hex 24)"
poner CLAVE_CIFRADO "$(openssl rand -base64 32 | tr '+/' '-_')"
poner DB_PATH /srv/datos/conector.db
poner PERMITIR_PUBLICAR false
poner PERMITIR_GESTIONAR false

# 3. Almacenamiento permanente para la base de datos (credenciales cifradas)
mkdir -p datos copias
chown 1000:1000 datos

cat > docker-compose.yml <<'YML'
services:
  multiconector:
    build: ./multiconector
    env_file: .env
    restart: unless-stopped
    expose: ["8080"]
    volumes:
      - ./datos:/srv/datos
  caddy:
    image: caddy:2
    restart: unless-stopped
    ports: ["80:80", "443:443", "443:443/udp"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ./site:/srv/site:ro
      - caddy_data:/data
      - caddy_config:/config
volumes:
  caddy_data:
  caddy_config:
YML

# 4. Copia diaria de la base de datos (30 días)
cat > /usr/local/bin/marketificial-copia <<'SH'
#!/bin/bash
set -euo pipefail
cd /opt/marketificial
[ -f datos/conector.db ] || exit 0
docker compose exec -T multiconector python -c "import sqlite3; s=sqlite3.connect('/srv/datos/conector.db'); d=sqlite3.connect('/srv/datos/copia.db'); s.backup(d); d.close()"
mv datos/copia.db "copias/conector-$(date +%F).db"
find copias -name 'conector-*.db' -mtime +30 -delete
SH
chmod 755 /usr/local/bin/marketificial-copia
echo "15 3 * * * root /usr/local/bin/marketificial-copia" > /etc/cron.d/marketificial-copia

# 5. Desplegar el código nuevo y esperar a que responda
/usr/local/bin/marketificial-deploy
docker compose up -d --build multiconector
docker compose restart caddy >/dev/null
echo -n "Esperando al conector"
for i in $(seq 1 60); do
  if docker compose exec -T multiconector python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/salud')" >/dev/null 2>&1; then
    echo " OK"; break
  fi
  echo -n "."; sleep 3
done

# 6. Resumen para César (solo root puede leerlo)
v() { grep "^$1=" .env | tail -1 | cut -d= -f2-; }
cat > /root/marketificial-credenciales.txt <<TXT
Marketificial · conector multicliente
Panel:           https://$DOMINIO/panel
Clave del panel: $(v API_KEY)
Conector para Claude (URL completa):
  https://$DOMINIO/mcp?token=$(v MCP_TOKEN)
Guarda también CLAVE_CIFRADO fuera del servidor (sin ella no se leen las credenciales guardadas):
  $(v CLAVE_CIFRADO)
Configuración: $APP/.env · Logs: cd $APP && docker compose logs -f multiconector
TXT
chmod 600 /root/marketificial-credenciales.txt
docker compose exec -T multiconector python -m app.cli comprobar || true
echo "== Listo. Credenciales en /root/marketificial-credenciales.txt =="
