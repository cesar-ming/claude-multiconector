"""Configuración del conector. Todo se lee de variables de entorno o del fichero .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Servicio ---
    base_url: str = "http://localhost:8000"  # URL pública (se usa para las redirecciones OAuth)
    api_key: str = ""  # clave para la API REST y el panel
    mcp_token: str = ""  # token para el servidor MCP (Bearer o ?token=)
    clave_cifrado: str = ""  # clave Fernet para cifrar credenciales
    db_path: str = "datos/conector.db"
    hosts_permitidos: str = ""  # dominios aceptados en la cabecera Host, separados por comas

    # --- Salvaguardas ---
    permitir_publicar: bool = False
    permitir_gestionar: bool = False
    max_subida_presupuesto_pct: float = 50.0  # subida máxima de presupuesto por acción
    max_presupuesto_diario: float = 500.0  # tope absoluto de presupuesto diario (moneda de la cuenta)
    caducidad_accion_min: int = 30

    # --- Versiones de API (centralizadas: se actualizan aquí) ---
    meta_version: str = "v27.0"
    google_ads_version: str = "v26"
    linkedin_version: str = "202610"
    tiktok_ads_version: str = "v1.3"
    shopify_version: str = "2026-10"

    # --- Meta (Ads, Página de Facebook, Instagram) ---
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_tipo_conversion: str = ""  # vacío = omni_purchase > purchase > offsite_conversion.fb_pixel_purchase

    # --- Google Ads ---
    google_client_id: str = ""
    google_client_secret: str = ""
    google_developer_token: str = ""
    google_login_customer_id: str = ""  # ID de tu MCC, sin guiones

    # --- LinkedIn (dos apps: Ads y Community Management) ---
    linkedin_ads_client_id: str = ""
    linkedin_ads_client_secret: str = ""
    linkedin_cm_client_id: str = ""
    linkedin_cm_client_secret: str = ""

    # --- TikTok Ads (API for Business) ---
    tiktok_ads_app_id: str = ""
    tiktok_ads_secret: str = ""
    tiktok_metrica_valor: str = ""  # métrica de valor de compra si la usas (vacío = no se informa)

    # --- TikTok orgánico (TikTok for Developers: Login Kit + Display + Content Posting) ---
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""

    # --- Shopify (app del Dev Dashboard) ---
    shopify_client_id: str = ""
    shopify_client_secret: str = ""
    shopify_scopes: str = "read_orders,read_products,write_products"

    def lista_hosts(self) -> list[str]:
        return [h.strip() for h in self.hosts_permitidos.split(",") if h.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
