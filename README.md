# Conector Marketificial

Un único servicio que conecta **Meta Ads, Google Ads, LinkedIn Ads, TikTok Ads, Página de Facebook, Instagram, TikTok, LinkedIn, Shopify y WordPress**, y los expone de dos formas:

- **Servidor MCP** (`/mcp`), para que Claude y los agentes de Marketificial lean datos, publiquen y gestionen campañas.
- **API REST** (`/api/...`) y un **panel web** (`/panel`), para conectar cuentas y para la futura plataforma de autoservicio.

Está pensado para que **la agencia** conecte las cuentas de sus clientes con sus propios accesos.

## Qué hace cada plataforma

| Plataforma | Leer | Publicar / gestionar |
|---|---|---|
| Meta Ads | Inversión, impresiones, clics, conversiones e ingresos por campaña, conjunto o anuncio y día · listado de campañas, conjuntos y anuncios | **Lanzar campaña completa en pausa** (campaña + conjunto + anuncio con imagen) · pausar/activar · cambiar presupuesto diario |
| Google Ads | Igual, por campaña y día · grupos, palabras clave y términos de búsqueda con métricas de 30 días | **Lanzar campaña de búsqueda en pausa** (validada por Google antes de confirmar) · añadir palabras clave y negativas · declarar campañas sin publicidad política de la UE · pausar/activar · cambiar presupuesto diario |
| LinkedIn Ads | Igual, por campaña y día | Pausar/activar · cambiar presupuesto diario |
| TikTok Ads | Igual, por campaña y día | Pausar/activar · cambiar presupuesto diario |
| Página de Facebook | Métricas diarias de la página · publicaciones | Publicar o programar post (texto, enlace o imagen) |
| Instagram | Alcance, seguidores, visualizaciones, interacciones · publicaciones | Publicar imagen, reel o carrusel |
| TikTok | Foto actual de la cuenta y métricas por vídeo | Publicar vídeo desde URL |
| LinkedIn | Estadísticas diarias de la página y seguidores · posts | Publicar post (texto, enlace o imagen) |
| Shopify | Pedidos y ventas por día (zona horaria de la tienda) · productos | Cambiar estado de producto · crear producto en borrador |
| WordPress | Entradas publicadas por día · entradas | Crear entrada (borrador por defecto) · actualizar entrada |

Todas las métricas de publicidad salen con el mismo formato:
`fecha, plataforma, cuenta_id, campana_id, campana, inversion, impresiones, clics, conversiones, ingresos, moneda`.

## Salvaguardas

- **Nada se publica ni se cambia en un paso.** `proponer_accion` consulta el estado actual y devuelve una vista previa con un token; solo `confirmar_accion` ejecuta. El token caduca a los 30 minutos y sirve una sola vez.
- **Interruptores generales**: `PERMITIR_PUBLICAR` y `PERMITIR_GESTIONAR` vienen desactivados.
- **Topes de presupuesto**: una acción no puede subir un presupuesto más de `MAX_SUBIDA_PRESUPUESTO_PCT` (50 %) ni superar `MAX_PRESUPUESTO_DIARIO` (500).
- **Comprobación de pertenencia**: no se puede tocar una campaña de otra cuenta.
- **Credenciales cifradas** en la base de datos (Fernet) y **registro de auditoría** de cada conexión, propuesta y ejecución, sin tokens ni secretos.
- **Sin datos inventados**: si una métrica no existe o una fuente falla, se devuelve el error o «sin dato», nunca una estimación. Los ingresos atribuidos de cada plataforma no se suman entre sí, y el MER solo se calcula si todas las fuentes responden y comparten moneda.

## Puesta en marcha en Clouding

El servidor se crea con la API de Clouding pasando [`deploy/clouding/userdata.sh`](deploy/clouding/userdata.sh) como `userData` (sustituyendo `__API_KEY__` y `__MCP_TOKEN__`). En el primer arranque, sin intervención:

- instala Docker, fail2ban, actualizaciones automáticas y 2 GB de swap;
- genera `CLAVE_CIFRADO` en el propio servidor y guarda la configuración en `/opt/marketificial/.env`;
- arranca Caddy con HTTPS automático para `mcp.marketificial.com` (conector y panel), `marketificial.com` (página provisional) y una dirección provisional `IP-con-guiones.sslip.io`;
- programa el despliegue continuo: **cada 5 minutos descarga `main` de este repositorio** y reconstruye si hay cambios. Si el repositorio es privado, necesita un token de solo lectura en `/opt/marketificial/github-token`;
- programa una copia diaria de la base de datos dentro del servidor (30 días).

Tamaño MVP: 0,5 vCPU, 1 GB de RAM y 10 GB SSD, sin copias de Clouding (unos 3,5 € al mes).

DNS: los registros A de `@`, `www` y `mcp` de `marketificial.com` deben apuntar a la IP del servidor. Caddy pide los certificados en cuanto resuelven.

Las claves de las apps de cada plataforma van en `/opt/marketificial/.env`; después, `cd /opt/marketificial && docker compose up -d multiconector`. `docker compose exec multiconector python -m app.cli comprobar` muestra qué falta y las URL de redirección de cada app.

Para otro servidor, sirven el `Dockerfile` y `docker-compose.yml` de la raíz detrás de cualquier proxy con HTTPS.

## Conectar las cuentas

1. Da de alta las apps de cada plataforma siguiendo **[docs/ALTA-APPS.md](docs/ALTA-APPS.md)** y pon sus claves en `.env`.
2. Entra en `https://mcp.marketificial.com/panel` con tu `API_KEY`.
3. Pulsa «Conectar» en cada plataforma y autoriza con tu usuario de la agencia.
4. Marca las cuentas que quieres usar y escribe el cliente (`echaleku`, `joselito`…).

WordPress y, si quieres, Meta (con token de usuario del sistema) y Shopify (con client ID y secret) se conectan con «credenciales manuales» desde el mismo panel. No pegues contraseñas ni tokens en un chat: usa siempre el panel.

## Usarlo desde Claude

Añade un conector personalizado con esta URL:

```
https://mcp.marketificial.com/mcp?token=TU_MCP_TOKEN
```

En Claude Code también puedes mandar el token en cabecera:

```bash
claude mcp add --transport http marketificial https://mcp.marketificial.com/mcp \
  --header "Authorization: Bearer TU_MCP_TOKEN"
```

Herramientas: `plataformas`, `conexiones`, `metricas`, `resumen_cliente`, `contenido`, `proponer_accion`, `confirmar_accion`, `cancelar_accion`, `enlace_para_conectar`, `cuentas_disponibles`, `vincular_cuenta` y `auditoria`.

Ejemplos de uso: «¿Cuánto hemos invertido en Echaleku este mes y cuál es el MER?», «Pausa la campaña de prospección de Meta de Echaleku», «Publica este carrusel en el Instagram de Joselito». Claude te enseñará la vista previa y esperará tu «sí» antes de ejecutar.

Si el token se filtra, cambia `MCP_TOKEN` en `.env` y reinicia (`docker compose up -d`).

## API REST

Todas las rutas `/api` piden la cabecera `X-API-Key`. La documentación interactiva está en `/docs`.

| Método | Ruta | Para qué |
|---|---|---|
| GET | `/api/catalogo` | Plataformas, acciones y salvaguardas |
| POST | `/api/oauth/{login}/enlace` | Enlace OAuth de un solo uso |
| POST | `/api/credenciales/manual` | Guardar credenciales manuales |
| GET | `/api/credenciales/{id}/cuentas` | Cuentas accesibles con una credencial |
| POST / GET | `/api/conexiones` | Vincular cuenta a cliente / listar |
| GET | `/api/conexiones/{id}/metricas?desde&hasta` | Métricas normalizadas |
| GET | `/api/conexiones/{id}/contenido` | Campañas, posts, productos o entradas |
| GET | `/api/clientes/{cliente}/resumen?desde&hasta` | Totales por plataforma, ventas y MER |
| POST | `/api/acciones` | Proponer acción (vista previa) |
| POST | `/api/acciones/{token}/confirmar` | Ejecutar |
| GET | `/api/auditoria` | Registro de actividad |

## Mantenimiento

- **Versiones de API**: están todas en `.env` (`META_VERSION`, `GOOGLE_ADS_VERSION`, `LINKEDIN_VERSION`, `SHOPIFY_VERSION`…). Meta, Google y LinkedIn retiran versiones cada pocos meses: revísalas cada trimestre.
- **Tokens que caducan**: Meta (60 días con login normal; usa un usuario del sistema para evitarlo) y LinkedIn (60 días si tu app no tiene refresh token). El panel marca en rojo las credenciales que caducan en menos de 7 días. Google, TikTok y Shopify se renuevan solos.
- **Pruebas**: `pip install -r requirements-dev.txt && pytest`.

## Límites conocidos

- Crear campañas: de momento Meta Ads (ventas, clientes potenciales, tráfico, reconocimiento; un conjunto y un anuncio de imagen) y Google Ads (búsqueda). Performance Max, Shopping, TikTok Ads y LinkedIn Ads, pendientes.
- Toda campaña nueva se crea en pausa; activarla es otra acción con su propia confirmación.
- Con la app de TikTok sin auditar, TikTok publica todos los vídeos como privados.
- Meta ha retirado varias métricas de página en 2026. Si una métrica ya no existe, se indica en `notas` y el resto se devuelve igual. Puedes pedir otras con el parámetro `metricas`.
- WordPress no guarda visitas. Para tráfico hace falta GA4 o Search Console, que no están en este conector.
- Shopify devuelve solo los últimos 60 días de pedidos si la app no tiene el permiso `read_all_orders`.
