# Multiconector MCP para Claude

[![CI](https://github.com/cesar-ming/claude-multiconector/actions/workflows/ci.yml/badge.svg)](https://github.com/cesar-ming/claude-multiconector/actions/workflows/ci.yml)

Servidor [MCP](https://modelcontextprotocol.io) que conecta Claude con **Google Ads, Meta Ads, LinkedIn Ads, TikTok Ads, Facebook, Instagram, LinkedIn, TikTok, Shopify y WordPress** desde un único conector.

Con él puedes pedirle a Claude cosas como:

- «¿Cuánto he gastado esta semana en todas las plataformas y cuál tiene mejor CPA?»
- «Compara las ventas de Shopify por fuente UTM con la inversión en Google y Meta de octubre.»
- «¿Qué posts de Instagram y TikTok del último mes tuvieron más engagement?»
- «Pausa la campaña 123 de Google Ads» (solo con escrituras activadas y tu confirmación).
- «Redacta un borrador en WordPress con el artículo que acabamos de escribir.»

## Instalación (menos de 5 minutos)

Requisitos: Node.js 20 o superior.

```bash
git clone https://github.com/cesar-ming/claude-multiconector.git
cd claude-multiconector
npm install
npm run build
```

### Claude Desktop

Edita `claude_desktop_config.json` (Ajustes → Desarrollador → Editar configuración) y añade el bloque de
[`claude_desktop_config.example.json`](claude_desktop_config.example.json) con la ruta absoluta a `dist/index.js`
y solo las credenciales de las plataformas que uses. Reinicia Claude.

### Claude Code

```bash
claude mcp add multiconector \
  -e META_ACCESS_TOKEN=... -e SHOPIFY_SHOP_DOMAIN=mitienda.myshopify.com -e SHOPIFY_ACCESS_TOKEN=... \
  -- node /ruta/absoluta/claude-multiconector/dist/index.js
```

Después pregunta a Claude «estado de los conectores» (herramienta `connectors_status`) para ver qué está activo.

## Modo remoto: claude.ai, app del iPhone y Android

En modo HTTP el multiconector se despliega en un servidor y se añade a claude.ai como **conector personalizado**.
Una vez añadido en la web, aparece también en las apps de Claude para iPhone, Android y escritorio.

```
Claude (web / iPhone / escritorio) ──OAuth──▶  https://tu-servidor/mcp  ──▶  Google, Meta, LinkedIn, TikTok, Shopify, WordPress
```

### Seguridad del modo remoto

- **OAuth 2.1 con PKCE y registro dinámico de clientes**, que es lo que usa claude.ai. Al conectar, se abre una página
  donde introduces tu contraseña de propietario (`MCP_OWNER_PASSWORD`); solo entonces Claude recibe un token.
- **Sin base de datos**: clientes, códigos y tokens van cifrados y firmados con AES-256-GCM (clave derivada de
  `MCP_TOKEN_SECRET`). Sobrevive a reinicios y a escalado a cero. Tokens de acceso de 1 hora y de refresco de 30 días.
- **Revocar todo**: cambia `MCP_OWNER_PASSWORD` o `MCP_TOKEN_SECRET` y todos los tokens emitidos dejan de valer.
- **Clave de API opcional** (`MCP_API_KEY`) para Claude Code o scripts que envían la cabecera `Authorization: Bearer`.
- El servidor **no arranca sin autenticación**, exige HTTPS en `MCP_PUBLIC_URL`, secretos de longitud mínima y limita
  los intentos de contraseña (10 cada 15 minutos por IP).

### Variables del modo remoto

| Variable | Obligatoria | Descripción |
|---|---|---|
| `MCP_TRANSPORT=http` | sí (o `--http`) | Activa el modo HTTP. La imagen Docker ya lo trae activado. |
| `MCP_PUBLIC_URL` | para OAuth | URL pública del servicio, p. ej. `https://multiconector-xxxx.run.app` |
| `MCP_OWNER_PASSWORD` | para OAuth | Tu contraseña para autorizar a Claude (mínimo 12 caracteres). |
| `MCP_TOKEN_SECRET` | para OAuth | Secreto aleatorio de 32+ caracteres: `openssl rand -base64 48` |
| `MCP_API_KEY` | opcional | Clave para clientes con cabeceras (32+ caracteres). |
| `PORT` | no | Puerto (por defecto 8080). |
| `MCP_TRUST_PROXY` | no | Saltos de proxy de confianza (por defecto 1, correcto en Cloud Run, Railway y Render). |

Más las credenciales de cada plataforma, igual que en modo local.

### Desplegar en Google Cloud Run

```bash
# 1. Guarda los secretos en Secret Manager (una vez)
printf '%s' 'tu-contraseña-larga' | gcloud secrets create mc-owner-password --data-file=-
openssl rand -base64 48 | tr -d '\n' | gcloud secrets create mc-token-secret --data-file=-
printf '%s' 'EAAB...' | gcloud secrets create mc-meta-token --data-file=-   # y así con cada credencial

# 2. Calcula la URL que tendrá el servicio (Cloud Run la forma con el número de proyecto)
REGION=europe-west1
PROJECT_NUMBER=$(gcloud projects describe "$(gcloud config get-value project)" --format='value(projectNumber)')
URL="https://claude-multiconector-${PROJECT_NUMBER}.${REGION}.run.app"

# 3. Despliega desde el código (Cloud Build usa el Dockerfile)
gcloud run deploy claude-multiconector --source . --region "$REGION" \
  --allow-unauthenticated \
  --set-env-vars MCP_PUBLIC_URL="$URL" \
  --set-secrets MCP_OWNER_PASSWORD=mc-owner-password:latest,MCP_TOKEN_SECRET=mc-token-secret:latest,META_ACCESS_TOKEN=mc-meta-token:latest
```

Si `gcloud` muestra al final una URL distinta de `$URL` (formato antiguo `...-xxxx-ew.a.run.app`), actualiza la variable:
`gcloud run services update claude-multiconector --region "$REGION" --update-env-vars MCP_PUBLIC_URL=https://...`

La cuenta de servicio de Cloud Run necesita el rol *Secret Manager Secret Accessor* sobre esos secretos (gcloud lo
ofrece la primera vez). `--allow-unauthenticated` es necesario porque la autenticación la hace el propio servidor con OAuth. Railway, Render
o Fly.io funcionan igual: despliegan el `Dockerfile` y solo hay que definir las mismas variables.

Comprueba que responde: `https://tu-servidor/healthz` debe devolver `{"ok":true,"oauth":true,...}`.

### Añadirlo a Claude

**claude.ai (y desde ahí, el iPhone):** Ajustes → Conectores → *Añadir conector personalizado* → URL
`https://tu-servidor/mcp` → *Conectar*. Se abrirá la página de autorización: introduce tu `MCP_OWNER_PASSWORD`. Después
el conector está disponible en la app del iPhone con la misma cuenta. (Los conectores personalizados dependen del plan
de Claude; en cuentas de equipo puede tener que añadirlo un administrador.)

**Claude Code con clave de API:**

```bash
claude mcp add --transport http multiconector https://tu-servidor/mcp --header "Authorization: Bearer $MCP_API_KEY"
```

**Probarlo en local:**

```bash
MCP_API_KEY=$(openssl rand -hex 24) META_ACCESS_TOKEN=... npm run build && node dist/index.js --http
```

## Conectores y herramientas

Cada conector se activa solo si están sus variables de entorno (ver [`.env.example`](.env.example)). Las herramientas de
los conectores sin credenciales no aparecen, así Claude no ve herramientas que no puede usar.

| Plataforma | Variables obligatorias | Lectura | Escritura (requiere `MCP_ALLOW_WRITES=true`) |
|---|---|---|---|
| **Todas** | — | `connectors_status`, `ads_cross_platform_summary` | — |
| **Google Ads** | `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_REFRESH_TOKEN` | `google_ads_list_accounts`, `google_ads_campaign_performance`, `google_ads_query` (GAQL solo SELECT) | `google_ads_set_campaign_status` |
| **Meta Ads** | `META_ACCESS_TOKEN` | `meta_ads_list_accounts`, `meta_ads_list_campaigns`, `meta_ads_performance` (cuenta/campaña/conjunto/anuncio, desgloses) | `meta_ads_set_status` |
| **Facebook** | `META_ACCESS_TOKEN` | `facebook_list_pages`, `facebook_page_posts`, `facebook_page_insights` | `facebook_publish_post` (publicar o programar) |
| **Instagram** | `META_ACCESS_TOKEN` | `instagram_account_info`, `instagram_list_media`, `instagram_media_insights`, `instagram_account_insights` | `instagram_publish` (imagen o reel) |
| **LinkedIn Ads** | `LINKEDIN_ACCESS_TOKEN` | `linkedin_ads_list_accounts`, `linkedin_ads_list_campaigns`, `linkedin_ads_performance` | `linkedin_ads_set_campaign_status` |
| **LinkedIn** (páginas de empresa) | `LINKEDIN_ACCESS_TOKEN` | `linkedin_organization_overview`, `linkedin_organization_posts` | `linkedin_publish_post` |
| **TikTok Ads** | `TIKTOK_ADS_ACCESS_TOKEN` | `tiktok_ads_list_advertisers`*, `tiktok_ads_list_campaigns`, `tiktok_ads_performance` | `tiktok_ads_set_campaign_status` |
| **TikTok** (orgánico) | `TIKTOK_ACCESS_TOKEN` | `tiktok_profile`, `tiktok_list_videos` | — |
| **Shopify** | `SHOPIFY_SHOP_DOMAIN`, `SHOPIFY_ACCESS_TOKEN` | `shopify_shop_info`, `shopify_sales_summary` (por día, canal y UTM de primer contacto), `shopify_top_products`, `shopify_graphql_query` (sin mutaciones) | — |
| **WordPress** | `WORDPRESS_SITE_URL`, `WORDPRESS_USERNAME`, `WORDPRESS_APPLICATION_PASSWORD` | `wordpress_site_info`, `wordpress_list_content`, `wordpress_get_content`, `wordpress_list_comments` | `wordpress_save_content` (borrador por defecto) |

\* Solo si además se configuran `TIKTOK_ADS_APP_ID` y `TIKTOK_ADS_APP_SECRET`.

Las herramientas de rendimiento devuelven métricas normalizadas (gasto, impresiones, clics, conversiones, valor) y KPIs
calculados: **CTR, CPC, CPA y ROAS**. Si no indicas fechas, el rango por defecto son los últimos 30 días completos.

`ads_cross_platform_summary` consulta en paralelo todas las plataformas de anuncios configuradas, calcula el reparto
del gasto y totales combinados **agrupados por moneda** (nunca suma monedas distintas). Si una plataforma falla, el
resto se devuelve igualmente y el error aparece en `errors`.

## Seguridad y privacidad

- **Escrituras desactivadas por defecto.** Publicar, pausar campañas o editar WordPress exige `MCP_ALLOW_WRITES=true`
  **y** el argumento `confirm=true`, que Claude solo debe enviar tras tu aprobación explícita. Las herramientas llevan
  las anotaciones MCP `readOnlyHint` / `destructiveHint` para que el cliente pida confirmación.
- **Credenciales solo en variables de entorno.** Nunca se escriben a disco ni se devuelven a Claude. Los mensajes de
  error se depuran (tokens, cabeceras `Bearer`, `access_token=`). Los tokens de página de Facebook se guardan solo en memoria.
- **Sin datos personales de clientes.** `shopify_sales_summary` no pide emails, nombres, teléfonos ni direcciones.
  `shopify_graphql_query` y `wordpress_list_comments` eliminan esos campos (y enmascaran emails en texto libre) antes de
  entregarlos a Claude.
- **Consultas acotadas.** GAQL solo `SELECT`; GraphQL de Shopify sin `mutation`/`subscription`; límites de filas y paginación.
- Usa tokens con el **mínimo de permisos** necesario (p. ej. usuario del sistema de Meta con acceso solo a los activos
  del cliente; app privada de Shopify con `read_orders` y `read_products`).

## Cómo obtener las credenciales

| Plataforma | Dónde | Permisos / scopes |
|---|---|---|
| Google Ads | Developer token en Centro de API de la MCC; cliente OAuth en Google Cloud; refresh token con el flujo OAuth (`https://www.googleapis.com/auth/adwords`) | Acceso básico o estándar del developer token |
| Meta | Business Manager → Usuarios del sistema → Generar token | `ads_read`, `ads_management`*, `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`*, `read_insights`, `instagram_basic`, `instagram_manage_insights`, `instagram_content_publish`*, `business_management` |
| LinkedIn | App en developer.linkedin.com con productos *Advertising API* y *Community Management API* | `r_ads`, `r_ads_reporting`, `rw_ads`*, `r_organization_social`, `w_organization_social`*, `rw_organization_admin` |
| TikTok Ads | TikTok for Business Developers → app → token de anunciante | Ads Management, Reporting |
| TikTok | developers.tiktok.com → Login Kit + Display API | `user.info.basic`, `user.info.stats`, `video.list` |
| Shopify | Admin → Configuración → Apps → Desarrollar apps → token de Admin API | `read_orders`, `read_products` (`read_all_orders` para >60 días) |
| WordPress | Usuarios → Perfil → Contraseñas de aplicación (requiere HTTPS) | Rol Editor o superior para crear contenido |

\* Solo necesarios para las herramientas de escritura.

### Versiones de API

Las plataformas retiran versiones con frecuencia. Todas son configurables: `GOOGLE_ADS_API_VERSION` (por defecto `v22`),
`META_API_VERSION` (`v24.0`), `LINKEDIN_API_VERSION` (`202510`), `TIKTOK_ADS_API_VERSION` (`v1.3`) y
`SHOPIFY_API_VERSION` (`2026-10`). Si una plataforma responde que la versión está obsoleta, sube la variable a la versión
vigente sin tocar código. Lo mismo con los nombres de métricas de insights de Facebook e Instagram: se pueden pasar en
el argumento `metrics`.

## Desarrollo

```bash
npm run typecheck   # TypeScript estricto
npm test            # tests con fetch simulado (sin llamadas reales) + flujo OAuth completo
npm run build
node scripts/smoke.mjs   # arranca el servidor compilado (stdio y HTTP) y lista herramientas
```

GitHub Actions ejecuta typecheck, tests, build y smoke test en Node 20, 22 y 24, y construye y prueba la imagen Docker, en cada push a `main` y en cada pull request.

Estructura:

```
src/
  index.ts            arranque (stdio o --http)
  httpServer.ts       modo remoto: Streamable HTTP + OAuth
  auth.ts             servidor OAuth sin estado (un propietario)
  server.ts           registro de conectores + connectors_status
  config.ts           lectura de variables de entorno
  http.ts             cliente HTTP con timeout y depuración de secretos
  privacy.ts          eliminación de datos personales
  util.ts             registro de herramientas, control de escrituras, fechas y KPIs
  connectors/
    googleAds.ts  meta.ts (Ads + Facebook + Instagram)  linkedin.ts (Ads + orgánico)
    tiktok.ts (Ads + orgánico)  shopify.ts  wordpress.ts  crossPlatform.ts
test/                 tests con cliente MCP en memoria
```

Para añadir una plataforma: crea `src/connectors/<plataforma>.ts` con una función `register<Plataforma>(server, ctx)`
que use `registerTool`, añade su configuración en `config.ts` y su fila en `connectorStatus` de `server.ts`.

## Limitaciones conocidas

- Los tests usan respuestas simuladas; antes de producción valida cada conector con una cuenta real (sobre todo
  nombres de métricas de insights, que Meta y LinkedIn cambian entre versiones).
- TikTok orgánico es solo lectura: la publicación por API exige una app auditada por TikTok.
- Las conversiones de cada plataforma siguen su propio modelo de atribución y no se deduplican entre sí.
- En modo remoto, un código de autorización ya usado se rechaza solo dentro de la misma instancia; si escalas a varias
  instancias, un código robado podría canjearse una segunda vez en otra durante sus 5 minutos de vida (PKCE sigue
  impidiendo que lo use quien no tenga el `code_verifier`).
- Modo remoto pensado para un único propietario (una contraseña). Para varios usuarios haría falta un proveedor de
  identidad externo.
