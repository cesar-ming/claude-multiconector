#!/bin/bash
# Marketificial MVP — first-boot provisioning (Clouding userData).
set -euo pipefail
exec > >(tee -a /var/log/marketificial-setup.log) 2>&1
echo "[$(date -Is)] start"
export DEBIAN_FRONTEND=noninteractive

apt-get update -y
apt-get install -y ca-certificates curl openssl fail2ban unattended-upgrades
if ! command -v docker >/dev/null; then curl -fsSL https://get.docker.com | sh; fi
docker compose version >/dev/null 2>&1 || apt-get install -y docker-compose-plugin
systemctl enable --now docker fail2ban

# 2 GB swap so builds never OOM on the small instance
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

APP=/opt/marketificial
mkdir -p $APP/site $APP/multiconector
apt-get install -y git

# Continuous deployment: pull main from GitHub every 5 min and rebuild on change.
# Private repo: put a read-only GitHub token in /opt/marketificial/github-token (chmod 600).
cat > /usr/local/bin/marketificial-deploy <<'DEPLOY'
#!/bin/bash
set -euo pipefail
APP=/opt/marketificial
DIR=$APP/multiconector
REPO=github.com/cesar-ming/claude-multiconector
AUTH=""
if [ -s $APP/github-token ]; then AUTH="x-access-token:$(tr -d '[:space:]' < $APP/github-token)@"; fi
CHANGED=0
if [ ! -d $DIR/.git ]; then
  rm -rf $DIR.tmp
  if ! GIT_TERMINAL_PROMPT=0 git clone -q --depth 1 "https://${AUTH}${REPO}.git" $DIR.tmp 2>/dev/null; then
    echo "repo not reachable yet (private and no token?)"; exit 0
  fi
  rm -rf $DIR && mv $DIR.tmp $DIR
  CHANGED=1
else
  git -C $DIR remote set-url origin "https://${AUTH}${REPO}.git"
  OLD=$(git -C $DIR rev-parse HEAD)
  GIT_TERMINAL_PROMPT=0 git -C $DIR fetch -q --depth 1 origin main
  git -C $DIR reset -q --hard origin/main
  [ "$OLD" != "$(git -C $DIR rev-parse HEAD)" ] && CHANGED=1
fi
cd $APP
if [ "$CHANGED" = 1 ] || [ -z "$(docker compose ps -q --status running multiconector)" ]; then
  echo "deploying $(git -C $DIR rev-parse --short HEAD)"
  docker compose up -d --build multiconector
  docker image prune -f >/dev/null
fi
DEPLOY
chmod 755 /usr/local/bin/marketificial-deploy
cat > /etc/systemd/system/marketificial-deploy.service <<'UNIT'
[Unit]
Description=Marketificial: pull and deploy multiconector
After=docker.service network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/bin/marketificial-deploy
UNIT
cat > /etc/systemd/system/marketificial-deploy.timer <<'UNIT'
[Unit]
Description=Marketificial: deploy every 5 minutes
[Timer]
OnBootSec=1min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
UNIT

# Secrets are generated on the server and never leave it.
if [ ! -f $APP/.env ]; then
  OWNER_PW=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
  cat > $APP/.env <<ENV
MCP_TRANSPORT=http
MCP_PUBLIC_URL=https://mcp.marketificial.com
MCP_OWNER_PASSWORD=${OWNER_PW}
MCP_TOKEN_SECRET=$(openssl rand -base64 48 | tr -d '\n')
MCP_API_KEY=$(openssl rand -hex 24)
MCP_TRUST_PROXY=1
MCP_ALLOW_WRITES=false
# --- Credenciales de plataformas: rellena las que uses y ejecuta: cd /opt/marketificial && docker compose up -d multiconector
# GOOGLE_ADS_DEVELOPER_TOKEN=
# GOOGLE_ADS_CLIENT_ID=
# GOOGLE_ADS_CLIENT_SECRET=
# GOOGLE_ADS_REFRESH_TOKEN=
# GOOGLE_ADS_CUSTOMER_ID=
# META_ACCESS_TOKEN=
# META_AD_ACCOUNT_ID=
# META_PAGE_ID=
# META_INSTAGRAM_ACCOUNT_ID=
# LINKEDIN_ACCESS_TOKEN=
# LINKEDIN_AD_ACCOUNT_ID=
# LINKEDIN_ORGANIZATION_ID=
# TIKTOK_ADS_ACCESS_TOKEN=
# TIKTOK_ADS_ADVERTISER_ID=
# TIKTOK_ACCESS_TOKEN=
# SHOPIFY_SHOP_DOMAIN=
# SHOPIFY_ACCESS_TOKEN=
# WORDPRESS_SITE_URL=
# WORDPRESS_USERNAME=
# WORDPRESS_APPLICATION_PASSWORD=
ENV
  chmod 600 $APP/.env
fi

cat > $APP/Caddyfile <<'CADDY'
mcp.marketificial.com {
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
<title>Marketificial</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;font:18px/1.5 system-ui,sans-serif;background:#f6f5f2;color:#1d1b18;padding:16px}@media(prefers-color-scheme:dark){body{background:#1b1a18;color:#f1eee8}}main{max-width:560px}h1{font-size:40px;margin:0 0 8px}p{opacity:.75}</style></head>
<body><main><h1>Marketificial</h1><p>Marketing con inteligencia artificial. Muy pronto.</p></main></body></html>
HTML

cat > $APP/docker-compose.yml <<'YML'
services:
  multiconector:
    build: ./multiconector
    env_file: .env
    restart: unless-stopped
    expose: ["8080"]
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

cd $APP && docker compose up -d caddy
systemctl daemon-reload
systemctl enable --now marketificial-deploy.timer
/usr/local/bin/marketificial-deploy || true

cat > /root/marketificial-credenciales.txt <<TXT
Marketificial MVP — credenciales (generadas en este servidor)
Conector para Claude:  https://mcp.marketificial.com/mcp
Contraseña de propietario (OAuth en claude.ai): $(grep ^MCP_OWNER_PASSWORD= $APP/.env | cut -d= -f2-)
API key (Claude Code):  $(grep ^MCP_API_KEY= $APP/.env | cut -d= -f2-)
Configuración: $APP/.env  ·  Logs: docker compose -f $APP/docker-compose.yml logs -f
TXT
chmod 600 /root/marketificial-credenciales.txt
echo "[$(date -Is)] done"
