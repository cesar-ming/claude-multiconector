# Alta de las apps en cada plataforma

Cada plataforma exige una app de desarrollador propia. Las URL de redirección siguen siempre este patrón:

```
https://mcp.marketificial.com/oauth/callback/<login>
```

`python -m app.cli comprobar` te las imprime todas con tu dominio.

Revisa los requisitos actuales en la consola de cada plataforma antes de empezar: cambian a menudo, y las revisiones de app pueden tardar de días a semanas. Empieza por las que no necesitan revisión para poder usar el conector mientras tanto.

---

## 1. Meta: Meta Ads, Página de Facebook e Instagram · login `meta`

**Opción recomendada para una agencia: usuario del sistema.**

1. En el Business Manager de la agencia: Configuración del negocio → Usuarios del sistema → crea uno con rol de administrador.
2. Asígnale las cuentas publicitarias, páginas y cuentas de Instagram de los clientes (las que tengas compartidas como socio).
3. Genera un token para tu app con estos permisos: `ads_read`, `ads_management`, `business_management`, `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `read_insights`, `instagram_basic`, `instagram_content_publish` e `instagram_manage_insights`.
4. En el panel, en la tarjeta de Meta → «Credenciales manuales», pega el token. No caduca.

**Opción OAuth** (si prefieres entrar con tu usuario):

1. En developers.facebook.com crea una app de tipo **Empresa**.
2. Añade «Inicio de sesión con Facebook para empresas» y la API de marketing.
3. En la configuración del inicio de sesión, añade la URL de redirección válida: `…/oauth/callback/meta`.
4. Copia el identificador y la clave secreta de la app en `META_APP_ID` y `META_APP_SECRET`.
5. Mientras la app esté en modo desarrollo, solo funciona con usuarios que tengan rol en la app (tú). Para el acceso avanzado, Meta pide verificación de la empresa y revisión de la app.

El token de usuario dura unos 60 días; luego hay que reconectar.

## 2. Google Ads · login `google`

1. **Token de desarrollador**: en tu cuenta de administrador (MCC) → Herramientas → Centro de API. Empieza con acceso de prueba, que solo funciona con cuentas de prueba. Solicita el **acceso básico** para trabajar con cuentas reales.
2. **Google Cloud Console**: crea un proyecto y habilita la «Google Ads API».
3. Pantalla de consentimiento OAuth: tipo externo, con el scope `https://www.googleapis.com/auth/adwords`. **Pásala a «En producción»**: en modo de prueba los refresh tokens caducan a los 7 días. Mientras no esté verificada, verás un aviso al autorizar; puedes continuar porque es tu propia app.
4. Credenciales → ID de cliente OAuth de tipo «Aplicación web», con la URI de redirección `…/oauth/callback/google`.
5. Rellena `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_DEVELOPER_TOKEN` y `GOOGLE_LOGIN_CUSTOMER_ID` (el ID de tu MCC, sin guiones). Con el MCC indicado, el conector lista todas las cuentas cliente que cuelgan de él.

## 3. LinkedIn Ads · login `linkedin_ads`

1. En developer.linkedin.com crea una app asociada a la página de empresa de la agencia y verifícala desde esa página.
2. Solicita el producto **Advertising API**. LinkedIn revisa la solicitud.
3. En Auth, añade la redirección `…/oauth/callback/linkedin_ads`. Scopes: `r_ads`, `r_ads_reporting` y `rw_ads`.
4. Rellena `LINKEDIN_ADS_CLIENT_ID` y `LINKEDIN_ADS_CLIENT_SECRET`.

## 4. LinkedIn, páginas de empresa · login `linkedin_organico`

LinkedIn exige que la **Community Management API** vaya en una app **distinta** de la de Ads.

1. Crea una segunda app, también asociada y verificada con la página de la agencia.
2. Solicita **Community Management API**. Hay un nivel de desarrollo y otro estándar, con revisión.
3. Redirección `…/oauth/callback/linkedin_organico`. Scopes: `r_organization_social`, `w_organization_social` y `rw_organization_admin`.
4. Rellena `LINKEDIN_CM_CLIENT_ID` y `LINKEDIN_CM_CLIENT_SECRET`.
5. Tu usuario debe ser administrador de las páginas de los clientes.

En las dos apps de LinkedIn el token dura 60 días. Solo las apps aprobadas para ello reciben refresh token; si la tuya no lo tiene, el panel te avisará para reconectar.

## 5. TikTok Ads · login `tiktok_ads`

1. Regístrate como desarrollador en business-api.tiktok.com y crea una app de Marketing API.
2. Marca los permisos de lectura de anuncios, gestión de anuncios e informes.
3. URL de redirección del anunciante: `…/oauth/callback/tiktok_ads`.
4. TikTok revisa la app. Cuando la aprueben, rellena `TIKTOK_ADS_APP_ID` y `TIKTOK_ADS_SECRET`.
5. Al autorizar, elige las cuentas publicitarias de los clientes a las que tienes acceso desde el Business Center.

Si quieres traer el valor de compra, pon en `TIKTOK_METRICA_VALOR` el nombre exacto de la métrica de valor que use tu cuenta en los informes de la API. Si lo dejas vacío, los ingresos de TikTok salen como «sin dato».

## 6. TikTok, cuenta orgánica · login `tiktok_organico`

1. En developers.tiktok.com crea una app y añade **Login Kit**, **Display API** (lista de vídeos) y **Content Posting API** con **Direct Post** activado.
2. Scopes: `user.info.basic`, `user.info.profile`, `user.info.stats`, `video.list` y `video.publish`.
3. Redirección `…/oauth/callback/tiktok_organico`. Verifica también las URL de tus términos y de tu política de privacidad.
4. **Verifica el dominio** desde el que se servirán los vídeos (por ejemplo `videos.marketificial.com`). TikTok solo descarga vídeos de dominios verificados.
5. Envía la app a revisión. Hasta que pase la auditoría, TikTok publica todo como privado y limita el número de cuentas que pueden publicar al día.
6. Rellena `TIKTOK_CLIENT_KEY` y `TIKTOK_CLIENT_SECRET`.

## 7. Shopify · login `shopify`

Desde el 1 de enero de 2026 ya no se pueden crear apps personalizadas desde el admin de la tienda: todas se crean en el **Dev Dashboard** (dev.shopify.com).

**Opción A, una app de la agencia para todos los clientes (OAuth):**

1. En el Dev Dashboard crea una app con los permisos `read_orders`, `read_products` y `write_products`. Añade `read_all_orders` si necesitas pedidos de hace más de 60 días; Shopify lo concede aparte.
2. URL de redirección: `…/oauth/callback/shopify`.
3. Distribución personalizada: genera el enlace de instalación para la tienda de cada cliente.
4. Rellena `SHOPIFY_CLIENT_ID` y `SHOPIFY_CLIENT_SECRET`. En el panel, escribe la tienda y pulsa «Conectar».

**Opción B, el cliente crea la app en su Dev Dashboard:** te pasa el *client ID* y el *client secret* (por un canal seguro) y los introduces en «Credenciales manuales». El conector pide un token nuevo cada 24 horas por su cuenta.

Si una tienda conserva una app personalizada antigua con token `shpat_…`, también puedes pegar ese token.

## 8. WordPress · login `wordpress`

No necesita app.

1. En el WordPress del cliente: Usuarios → tu perfil → **Contraseñas de aplicación** → crea una llamada «Marketificial».
2. Necesitas rol de Autor, Editor o Administrador, y la web debe ir por HTTPS.
3. En el panel → WordPress → introduce la URL del sitio, tu usuario y la contraseña de aplicación.

Si falla, revisa que ningún plugin de seguridad bloquee la API REST (`/wp-json/`).
