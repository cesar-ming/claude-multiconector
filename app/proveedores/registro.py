"""Registro de logins y plataformas."""
from .base import Login, Plataforma
from .google_ads import GoogleAds, LoginGoogle
from .linkedin import LinkedIn, LinkedInAds, LoginLinkedInAds, LoginLinkedInOrganico
from .meta import Facebook, Instagram, LoginMeta, MetaAds
from .shopify import LoginShopify, Shopify
from .tiktok import LoginTikTokAds, LoginTikTokOrganico, TikTok, TikTokAds
from .wordpress import LoginWordPress, WordPress

LOGINS: dict[str, Login] = {l.id: l for l in (
    LoginMeta(), LoginGoogle(), LoginLinkedInAds(), LoginLinkedInOrganico(), LoginTikTokAds(),
    LoginTikTokOrganico(), LoginShopify(), LoginWordPress())}

PLATAFORMAS: dict[str, Plataforma] = {p.id: p for p in (
    MetaAds(), GoogleAds(), LinkedInAds(), TikTokAds(), Facebook(), Instagram(), TikTok(), LinkedIn(),
    Shopify(), WordPress())}
