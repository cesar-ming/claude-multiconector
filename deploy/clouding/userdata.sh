#!/bin/bash
# Marketificial MVP · primer arranque del servidor en Clouding (campo userData, máx. 16 000 caracteres).
# Instala el conector multicliente con Caddy (HTTPS automático) y despliegue continuo desde GitHub.
# __API_KEY__ y __MCP_TOKEN__ se sustituyen al crear el servidor; CLAVE_CIFRADO se genera aquí y no sale del servidor.
set -euo pipefail
exec > >(tee -a /var/log/marketificial-setup.log) 2>&1
echo "[$(date -Is)] inicio"
export DEBIAN_FRONTEND=noninteractive
APP=/opt/marketificial
REPO=github.com/cesar-ming/claude-multiconector

apt-get update -y
apt-get install -y ca-certificates curl openssl git fail2ban unattended-upgrades
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
docker compose version >/dev/null 2>&1 || apt-get install -y docker-compose-plugin
systemctl enable --now docker fail2ban

# 2 GB de swap: el servidor solo tiene 1 GB de RAM
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

mkdir -p $APP/site $APP/datos $APP/copias
chown 1000:1000 $APP/datos
IP=$(curl -fsS https://api.ipify.org || hostname -I | awk '{print $1}')
HOST_IP="$(echo "$IP" | tr . -).sslip.io"

if [ ! -f $APP/.env ]; then
  cat > $APP/.env <<ENV
BASE_URL=https://mcp.marketificial.com
HOSTS_PERMITIDOS=mcp.marketificial.com,$HOST_IP
API_KEY=__API_KEY__
MCP_TOKEN=__MCP_TOKEN__
CLAVE_CIFRADO=$(openssl rand -base64 32 | tr '+/' '-_')
DB_PATH=/srv/datos/conector.db
PERMITIR_PUBLICAR=false
PERMITIR_GESTIONAR=false
# Claves de las apps de cada plataforma (docs/ALTA-APPS.md). Tras editar:
#   cd /opt/marketificial && docker compose up -d multiconector
ENV
  chmod 600 $APP/.env
fi

cat > $APP/Caddyfile <<CADDY
mcp.marketificial.com, $HOST_IP {
	encode gzip
	reverse_proxy multiconector:8080
}
marketificial.com {
	encode gzip
	root * /srv/site
	file_server
}
www.marketificial.com {
	redir https://marketificial.com{uri} permanent
}
CADDY

cat > $APP/site/index.html <<'HTML'
<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Marketificial</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;font:18px/1.5 system-ui,sans-serif;background:#f6f5f2;color:#1d1b18;padding:16px}main{max-width:560px}h1{font-size:40px;margin:0 0 8px}p{opacity:.75}</style></head>
<body><main><h1>Marketificial</h1><p>Marketing 360 con inteligencia artificial. Muy pronto.</p></main></body></html>
HTML

cat > $APP/docker-compose.yml <<'YML'
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

# Despliegue continuo: cada 5 minutos baja main y reconstruye si hay cambios.
# Si el repositorio es privado, guarda un token de solo lectura en /opt/marketificial/github-token (chmod 600).
cat > /usr/local/bin/marketificial-deploy <<DEPLOY
#!/bin/bash
set -euo pipefail
APP=$APP
DIR=\$APP/multiconector
AUTH=""
[ -s \$APP/github-token ] && AUTH="x-access-token:\$(tr -d '[:space:]' < \$APP/github-token)@"
CHANGED=0
if [ ! -d \$DIR/.git ]; then
  rm -rf \$DIR.tmp
  GIT_TERMINAL_PROMPT=0 git clone -q --depth 1 "https://\${AUTH}$REPO.git" \$DIR.tmp || { echo "repositorio no accesible"; exit 0; }
  rm -rf \$DIR && mv \$DIR.tmp \$DIR && CHANGED=1
else
  git -C \$DIR remote set-url origin "https://\${AUTH}$REPO.git"
  OLD=\$(git -C \$DIR rev-parse HEAD)
  GIT_TERMINAL_PROMPT=0 git -C \$DIR fetch -q --depth 1 origin main || { echo "repositorio no accesible"; exit 0; }
  git -C \$DIR reset -q --hard origin/main
  [ "\$OLD" != "\$(git -C \$DIR rev-parse HEAD)" ] && CHANGED=1
fi
cd \$APP
if [ "\$CHANGED" = 1 ] || [ -z "\$(docker compose ps -q --status running multiconector)" ]; then
  echo "desplegando \$(git -C \$DIR rev-parse --short HEAD)"
  docker compose up -d --build multiconector
  docker image prune -f >/dev/null
fi
DEPLOY
chmod 755 /usr/local/bin/marketificial-deploy
cat > /etc/systemd/system/marketificial-deploy.service <<'UNIT'
[Unit]
Description=Marketificial: despliegue del conector
After=docker.service network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/bin/marketificial-deploy
UNIT
cat > /etc/systemd/system/marketificial-deploy.timer <<'UNIT'
[Unit]
Description=Marketificial: despliegue cada 5 minutos
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
UNIT

# Copia diaria de la base de datos (30 días) dentro del servidor
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

/usr/local/bin/marketificial-deploy || true
cd $APP && docker compose up -d
systemctl daemon-reload
systemctl enable --now marketificial-deploy.timer

cat > /root/marketificial-credenciales.txt <<TXT
Marketificial · conector multicliente
Panel:  https://mcp.marketificial.com/panel   (provisional: https://$HOST_IP/panel)
Claves: API_KEY y MCP_TOKEN en $APP/.env
Guarda CLAVE_CIFRADO (en $APP/.env) fuera del servidor: sin ella no se leen las credenciales guardadas.
TXT
chmod 600 /root/marketificial-credenciales.txt
echo "[$(date -Is)] listo · https://$HOST_IP"
