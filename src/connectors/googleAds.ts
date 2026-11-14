import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { GoogleAdsConfig } from "../config.js";
import { type AdMetrics, type Context, dateRangeShape, num, registerTool, requireValue, resolveRange, withKpis } from "../util.js";

const PLATFORM = "Google Ads";

let cachedToken: { value: string; expiresAt: number; key: string } | undefined;

export function resetGoogleTokenCache(): void {
  cachedToken = undefined;
}

async function accessToken(ctx: Context, cfg: GoogleAdsConfig): Promise<string> {
  const now = ctx.now().getTime();
  if (cachedToken && cachedToken.key === cfg.clientId && cachedToken.expiresAt > now + 60_000) {
    return cachedToken.value;
  }
  const res = await ctx.http.json<{ access_token: string; expires_in?: number }>(PLATFORM, {
    method: "POST",
    url: "https://oauth2.googleapis.com/token",
    form: {
      client_id: cfg.clientId,
      client_secret: cfg.clientSecret,
      refresh_token: cfg.refreshToken,
      grant_type: "refresh_token",
    },
  });
  cachedToken = { value: res.access_token, expiresAt: now + (res.expires_in ?? 3600) * 1000, key: cfg.clientId };
  return res.access_token;
}

async function headers(ctx: Context, cfg: GoogleAdsConfig): Promise<Record<string, string>> {
  const h: Record<string, string> = {
    Authorization: `Bearer ${await accessToken(ctx, cfg)}`,
    "developer-token": cfg.developerToken,
  };
  if (cfg.loginCustomerId) h["login-customer-id"] = cfg.loginCustomerId;
  return h;
}

function customerId(cfg: GoogleAdsConfig, value?: string): string {
  const id = (value ?? cfg.defaultCustomerId ?? "").replace(/\D/g, "");
  return requireValue(id || undefined, "customer_id (GOOGLE_ADS_CUSTOMER_ID)");
}

function base(cfg: GoogleAdsConfig): string {
  return `https://googleads.googleapis.com/${cfg.apiVersion}`;
}

export async function gaqlSearch(ctx: Context, cfg: GoogleAdsConfig, cid: string, query: string, maxRows = 1000): Promise<any[]> {
  const rows: any[] = [];
  let pageToken: string | undefined;
  do {
    const res = await ctx.http.json<{ results?: any[]; nextPageToken?: string }>(PLATFORM, {
      method: "POST",
      url: `${base(cfg)}/customers/${cid}/googleAds:search`,
      headers: await headers(ctx, cfg),
      body: pageToken ? { query, pageToken } : { query },
    });
    rows.push(...(res.results ?? []));
    pageToken = res.nextPageToken;
  } while (pageToken && rows.length < maxRows);
  return rows.slice(0, maxRows);
}

export async function googleAdsMetrics(ctx: Context, range: { from: string; to: string }, account?: string): Promise<AdMetrics> {
  const cfg = ctx.config.googleAds!;
  const cid = customerId(cfg, account);
  const rows = await gaqlSearch(
    ctx,
    cfg,
    cid,
    `SELECT customer.currency_code, metrics.cost_micros, metrics.impressions, metrics.clicks, metrics.conversions, metrics.conversions_value FROM customer WHERE segments.date BETWEEN '${range.from}' AND '${range.to}'`,
  );
  const m: AdMetrics = { platform: PLATFORM, account: cid, spend: 0, impressions: 0, clicks: 0, conversions: 0, conversionValue: 0 };
  for (const r of rows) {
    m.currency = r.customer?.currencyCode ?? m.currency;
    m.spend += num(r.metrics?.costMicros) / 1e6;
    m.impressions += num(r.metrics?.impressions);
    m.clicks += num(r.metrics?.clicks);
    m.conversions += num(r.metrics?.conversions);
    m.conversionValue! += num(r.metrics?.conversionsValue);
  }
  return m;
}

export function registerGoogleAds(server: McpServer, ctx: Context): void {
  const cfg = ctx.config.googleAds;
  if (!cfg) return;

  registerTool(server, ctx, {
    name: "google_ads_list_accounts",
    title: "Google Ads: list accessible accounts",
    description: "Lists the Google Ads customer accounts accessible with the configured credentials.",
    input: {},
    handler: async () => {
      const res = await ctx.http.json<{ resourceNames?: string[] }>(PLATFORM, {
        url: `${base(cfg)}/customers:listAccessibleCustomers`,
        headers: await headers(ctx, cfg),
      });
      return { customers: (res.resourceNames ?? []).map((r) => r.replace("customers/", "")) };
    },
  });

  registerTool(server, ctx, {
    name: "google_ads_campaign_performance",
    title: "Google Ads: campaign performance",
    description: "Spend, impressions, clicks, conversions, CTR, CPC, CPA and ROAS per campaign for a date range.",
    input: {
      customer_id: z.string().optional().describe("Google Ads customer ID (digits, dashes allowed)."),
      ...dateRangeShape,
      include_paused: z.boolean().optional().describe("Include paused campaigns (default false)."),
    },
    handler: async (args) => {
      const cid = customerId(cfg, args.customer_id);
      const range = resolveRange(ctx.now(), args.date_from, args.date_to);
      const statusFilter = args.include_paused ? "AND campaign.status != 'REMOVED'" : "AND campaign.status = 'ENABLED'";
      const rows = await gaqlSearch(
        ctx,
        cfg,
        cid,
        `SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, metrics.cost_micros, metrics.impressions, metrics.clicks, metrics.conversions, metrics.conversions_value FROM campaign WHERE segments.date BETWEEN '${range.from}' AND '${range.to}' ${statusFilter} ORDER BY metrics.cost_micros DESC`,
      );
      return {
        customer_id: cid,
        range,
        campaigns: rows.map((r) =>
          withKpis({
            id: r.campaign?.id,
            name: r.campaign?.name,
            status: r.campaign?.status,
            channel: r.campaign?.advertisingChannelType,
            spend: num(r.metrics?.costMicros) / 1e6,
            impressions: num(r.metrics?.impressions),
            clicks: num(r.metrics?.clicks),
            conversions: num(r.metrics?.conversions),
            conversionValue: num(r.metrics?.conversionsValue),
          }),
        ),
      };
    },
  });

  registerTool(server, ctx, {
    name: "google_ads_query",
    title: "Google Ads: run GAQL query",
    description:
      "Runs a read-only Google Ads Query Language (GAQL) query, e.g. keyword, search term, ad group or asset reports. Returns raw rows (cost is in micros).",
    input: {
      customer_id: z.string().optional(),
      query: z.string().min(10).describe("GAQL SELECT statement."),
      max_rows: z.number().int().min(1).max(10000).optional().describe("Default 500."),
    },
    handler: async (args) => {
      if (!/^\s*select\s/i.test(args.query)) throw new Error("Only GAQL SELECT queries are allowed.");
      const cid = customerId(cfg, args.customer_id);
      const rows = await gaqlSearch(ctx, cfg, cid, args.query, args.max_rows ?? 500);
      return { customer_id: cid, row_count: rows.length, rows };
    },
  });

  registerTool(server, ctx, {
    name: "google_ads_set_campaign_status",
    title: "Google Ads: pause or enable a campaign",
    description: "Sets a Google Ads campaign status to ENABLED or PAUSED.",
    write: true,
    input: {
      customer_id: z.string().optional(),
      campaign_id: z.string().regex(/^\d+$/),
      status: z.enum(["ENABLED", "PAUSED"]),
    },
    handler: async (args) => {
      const cid = customerId(cfg, args.customer_id);
      return ctx.http.json(PLATFORM, {
        method: "POST",
        url: `${base(cfg)}/customers/${cid}/campaigns:mutate`,
        headers: await headers(ctx, cfg),
        body: {
          operations: [
            { update: { resourceName: `customers/${cid}/campaigns/${args.campaign_id}`, status: args.status }, updateMask: "status" },
          ],
        },
      });
    },
  });
}
