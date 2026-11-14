import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type AppConfig, collectSecrets } from "./config.js";
import { registerCrossPlatform } from "./connectors/crossPlatform.js";
import { registerGoogleAds } from "./connectors/googleAds.js";
import { registerLinkedIn } from "./connectors/linkedin.js";
import { registerMeta } from "./connectors/meta.js";
import { registerShopify } from "./connectors/shopify.js";
import { registerTikTok, registerTikTokAds } from "./connectors/tiktok.js";
import { registerWordPress } from "./connectors/wordpress.js";
import { type FetchLike, HttpClient } from "./http.js";
import { type Context, registerTool } from "./util.js";

export const VERSION = "0.1.0";

export function connectorStatus(config: AppConfig) {
  const row = (name: string, configured: boolean, env: string, covers: string) => ({ name, configured, covers, env });
  return [
    row("Google Ads", !!config.googleAds, "GOOGLE_ADS_DEVELOPER_TOKEN, GOOGLE_ADS_CLIENT_ID, GOOGLE_ADS_CLIENT_SECRET, GOOGLE_ADS_REFRESH_TOKEN", "Google Ads"),
    row("Meta", !!config.meta, "META_ACCESS_TOKEN", "Meta Ads, Facebook Pages, Instagram"),
    row("LinkedIn", !!config.linkedin, "LINKEDIN_ACCESS_TOKEN", "LinkedIn Ads, LinkedIn company pages"),
    row("TikTok Ads", !!config.tiktokAds, "TIKTOK_ADS_ACCESS_TOKEN", "TikTok Ads"),
    row("TikTok", !!config.tiktok, "TIKTOK_ACCESS_TOKEN", "TikTok organic profile and videos"),
    row("Shopify", !!config.shopify, "SHOPIFY_SHOP_DOMAIN, SHOPIFY_ACCESS_TOKEN", "Shopify store"),
    row("WordPress", !!config.wordpress, "WORDPRESS_SITE_URL, WORDPRESS_USERNAME, WORDPRESS_APPLICATION_PASSWORD", "WordPress site"),
  ];
}

export function buildServer(config: AppConfig, fetchImpl: FetchLike = fetch, now: () => Date = () => new Date()): McpServer {
  const server = new McpServer(
    { name: "claude-multiconector", version: VERSION },
    {
      instructions:
        "Multiconnector for marketing data: Google Ads, Meta Ads, LinkedIn Ads, TikTok Ads, Facebook, Instagram, LinkedIn, TikTok, Shopify and WordPress. " +
        "Call connectors_status first to see which platforms are configured. Prefer ads_cross_platform_summary for blended paid-media questions. " +
        "Write tools (publishing, pausing, editing) need MCP_ALLOW_WRITES=true and confirm=true: always show the user the exact change and get explicit approval before setting confirm=true.",
    },
  );
  const ctx: Context = { config, http: new HttpClient(fetchImpl, config.timeoutMs, collectSecrets(config)), now };

  registerTool(server, ctx, {
    name: "connectors_status",
    title: "Connectors status",
    description: "Shows which platforms are configured, which environment variables each needs, and whether write tools are enabled.",
    input: {},
    handler: async () => ({ version: VERSION, writes_enabled: config.allowWrites, connectors: connectorStatus(config) }),
  });

  registerGoogleAds(server, ctx);
  registerMeta(server, ctx);
  registerLinkedIn(server, ctx);
  registerTikTokAds(server, ctx);
  registerTikTok(server, ctx);
  registerShopify(server, ctx);
  registerWordPress(server, ctx);
  registerCrossPlatform(server, ctx);
  return server;
}
