/**
 * Configuration is read exclusively from environment variables so that no
 * credential is ever written to disk by this server. A connector is enabled
 * only when all of its required variables are present.
 */

export type Env = Record<string, string | undefined>;

export interface GoogleAdsConfig {
  developerToken: string;
  clientId: string;
  clientSecret: string;
  refreshToken: string;
  loginCustomerId?: string;
  defaultCustomerId?: string;
  apiVersion: string;
}

export interface MetaConfig {
  accessToken: string;
  defaultAdAccountId?: string;
  defaultPageId?: string;
  defaultInstagramAccountId?: string;
  apiVersion: string;
}

export interface LinkedInConfig {
  accessToken: string;
  defaultAdAccountId?: string;
  defaultOrganizationId?: string;
  apiVersion: string;
}

export interface TikTokAdsConfig {
  accessToken: string;
  appId?: string;
  appSecret?: string;
  defaultAdvertiserId?: string;
  apiVersion: string;
}

export interface TikTokConfig {
  accessToken: string;
}

export interface ShopifyConfig {
  shopDomain: string;
  accessToken: string;
  apiVersion: string;
}

export interface WordPressConfig {
  siteUrl: string;
  username: string;
  applicationPassword: string;
}

export interface AppConfig {
  allowWrites: boolean;
  timeoutMs: number;
  googleAds?: GoogleAdsConfig;
  meta?: MetaConfig;
  linkedin?: LinkedInConfig;
  tiktokAds?: TikTokAdsConfig;
  tiktok?: TikTokConfig;
  shopify?: ShopifyConfig;
  wordpress?: WordPressConfig;
}

function get(env: Env, key: string): string | undefined {
  const value = env[key]?.trim();
  return value ? value : undefined;
}

function digits(value: string | undefined): string | undefined {
  return value ? value.replace(/\D/g, "") || undefined : undefined;
}

export function loadConfig(env: Env = process.env): AppConfig {
  const config: AppConfig = {
    allowWrites: /^(1|true|yes)$/i.test(get(env, "MCP_ALLOW_WRITES") ?? ""),
    timeoutMs: Number(get(env, "MCP_HTTP_TIMEOUT_MS") ?? 30000) || 30000,
  };

  const gDev = get(env, "GOOGLE_ADS_DEVELOPER_TOKEN");
  const gId = get(env, "GOOGLE_ADS_CLIENT_ID");
  const gSecret = get(env, "GOOGLE_ADS_CLIENT_SECRET");
  const gRefresh = get(env, "GOOGLE_ADS_REFRESH_TOKEN");
  if (gDev && gId && gSecret && gRefresh) {
    config.googleAds = {
      developerToken: gDev,
      clientId: gId,
      clientSecret: gSecret,
      refreshToken: gRefresh,
      loginCustomerId: digits(get(env, "GOOGLE_ADS_LOGIN_CUSTOMER_ID")),
      defaultCustomerId: digits(get(env, "GOOGLE_ADS_CUSTOMER_ID")),
      apiVersion: get(env, "GOOGLE_ADS_API_VERSION") ?? "v22",
    };
  }

  const metaToken = get(env, "META_ACCESS_TOKEN");
  if (metaToken) {
    config.meta = {
      accessToken: metaToken,
      defaultAdAccountId: get(env, "META_AD_ACCOUNT_ID"),
      defaultPageId: get(env, "META_PAGE_ID"),
      defaultInstagramAccountId: get(env, "META_INSTAGRAM_ACCOUNT_ID"),
      apiVersion: get(env, "META_API_VERSION") ?? "v24.0",
    };
  }

  const liToken = get(env, "LINKEDIN_ACCESS_TOKEN");
  if (liToken) {
    config.linkedin = {
      accessToken: liToken,
      defaultAdAccountId: digits(get(env, "LINKEDIN_AD_ACCOUNT_ID")),
      defaultOrganizationId: digits(get(env, "LINKEDIN_ORGANIZATION_ID")),
      apiVersion: get(env, "LINKEDIN_API_VERSION") ?? "202510",
    };
  }

  const ttAdsToken = get(env, "TIKTOK_ADS_ACCESS_TOKEN");
  if (ttAdsToken) {
    config.tiktokAds = {
      accessToken: ttAdsToken,
      appId: get(env, "TIKTOK_ADS_APP_ID"),
      appSecret: get(env, "TIKTOK_ADS_APP_SECRET"),
      defaultAdvertiserId: get(env, "TIKTOK_ADS_ADVERTISER_ID"),
      apiVersion: get(env, "TIKTOK_ADS_API_VERSION") ?? "v1.3",
    };
  }

  const ttToken = get(env, "TIKTOK_ACCESS_TOKEN");
  if (ttToken) {
    config.tiktok = { accessToken: ttToken };
  }

  const shopDomain = get(env, "SHOPIFY_SHOP_DOMAIN");
  const shopToken = get(env, "SHOPIFY_ACCESS_TOKEN");
  if (shopDomain && shopToken) {
    config.shopify = {
      shopDomain: shopDomain.replace(/^https?:\/\//, "").replace(/\/+$/, ""),
      accessToken: shopToken,
      apiVersion: get(env, "SHOPIFY_API_VERSION") ?? "2026-10",
    };
  }

  const wpUrl = get(env, "WORDPRESS_SITE_URL");
  const wpUser = get(env, "WORDPRESS_USERNAME");
  const wpPass = get(env, "WORDPRESS_APPLICATION_PASSWORD");
  if (wpUrl && wpUser && wpPass) {
    config.wordpress = {
      siteUrl: wpUrl.replace(/\/+$/, ""),
      username: wpUser,
      applicationPassword: wpPass,
    };
  }

  return config;
}

/** Collects every secret value so error messages can be scrubbed. */
export function collectSecrets(config: AppConfig): string[] {
  const secrets = [
    config.googleAds?.developerToken,
    config.googleAds?.clientSecret,
    config.googleAds?.refreshToken,
    config.meta?.accessToken,
    config.linkedin?.accessToken,
    config.tiktokAds?.accessToken,
    config.tiktokAds?.appSecret,
    config.tiktok?.accessToken,
    config.shopify?.accessToken,
    config.wordpress?.applicationPassword,
  ];
  return secrets.filter((s): s is string => typeof s === "string" && s.length >= 6);
}
