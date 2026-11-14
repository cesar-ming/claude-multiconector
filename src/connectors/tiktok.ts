import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { TikTokAdsConfig } from "../config.js";
import { type AdMetrics, type Context, dateRangeShape, num, registerTool, requireValue, resolveRange, withKpis } from "../util.js";

const ADS = "TikTok Ads";
const TT = "TikTok";

// ---------------------------------------------------------------- TikTok Ads (Business API)

async function ads<T = any>(
  ctx: Context,
  path: string,
  opts: { query?: Record<string, string | number | undefined>; body?: unknown } = {},
): Promise<T> {
  const cfg = ctx.config.tiktokAds!;
  const res = await ctx.http.json<{ code: number; message?: string; data?: T }>(ADS, {
    method: opts.body ? "POST" : "GET",
    url: `https://business-api.tiktok.com/open_api/${cfg.apiVersion}/${path}`,
    headers: { "Access-Token": cfg.accessToken },
    query: opts.query,
    body: opts.body,
  });
  if (res.code !== 0) throw new Error(`${ADS}: API error ${res.code} ${res.message ?? ""}`.trim());
  return res.data as T;
}

function advertiser(cfg: TikTokAdsConfig, v?: string): string {
  const id = requireValue(v ?? cfg.defaultAdvertiserId, "advertiser_id (TIKTOK_ADS_ADVERTISER_ID)");
  if (!/^\d+$/.test(id)) throw new Error("advertiser_id must be numeric.");
  return id;
}

async function report(ctx: Context, advertiserId: string, range: { from: string; to: string }, level: "advertiser" | "campaign" | "adgroup" | "ad") {
  const map = {
    advertiser: { data_level: "AUCTION_ADVERTISER", dimension: "advertiser_id", name: undefined },
    campaign: { data_level: "AUCTION_CAMPAIGN", dimension: "campaign_id", name: "campaign_name" },
    adgroup: { data_level: "AUCTION_ADGROUP", dimension: "adgroup_id", name: "adgroup_name" },
    ad: { data_level: "AUCTION_AD", dimension: "ad_id", name: "ad_name" },
  }[level];
  const metrics = ["spend", "impressions", "clicks", "conversion", "reach", ...(map.name ? [map.name] : [])];
  const rows: any[] = [];
  let page = 1;
  let totalPages = 1;
  do {
    const data = await ads<{ list?: any[]; page_info?: { total_page?: number } }>(ctx, "report/integrated/get/", {
      query: {
        advertiser_id: advertiserId,
        report_type: "BASIC",
        data_level: map.data_level,
        dimensions: JSON.stringify([map.dimension]),
        metrics: JSON.stringify(metrics),
        start_date: range.from,
        end_date: range.to,
        page,
        page_size: 1000,
      },
    });
    rows.push(...(data.list ?? []));
    totalPages = data.page_info?.total_page ?? 1;
    page++;
  } while (page <= totalPages && page <= 10);
  return rows.map((r) => ({
    id: r.dimensions?.[map.dimension],
    name: map.name ? r.metrics?.[map.name] : undefined,
    spend: num(r.metrics?.spend),
    impressions: num(r.metrics?.impressions),
    clicks: num(r.metrics?.clicks),
    conversions: num(r.metrics?.conversion),
    reach: num(r.metrics?.reach),
  }));
}

export async function tiktokAdsMetrics(ctx: Context, range: { from: string; to: string }, account?: string): Promise<AdMetrics> {
  const id = advertiser(ctx.config.tiktokAds!, account);
  const rows = await report(ctx, id, range, "advertiser");
  const m: AdMetrics = { platform: ADS, account: id, spend: 0, impressions: 0, clicks: 0, conversions: 0 };
  for (const r of rows) {
    m.spend += r.spend;
    m.impressions += r.impressions;
    m.clicks += r.clicks;
    m.conversions += r.conversions;
  }
  return m;
}

export function registerTikTokAds(server: McpServer, ctx: Context): void {
  const cfg = ctx.config.tiktokAds;
  if (!cfg) return;

  if (cfg.appId && cfg.appSecret) {
    registerTool(server, ctx, {
      name: "tiktok_ads_list_advertisers",
      title: "TikTok Ads: list advertisers",
      description: "Lists the TikTok advertiser accounts authorised for the access token.",
      input: {},
      handler: async () =>
        ads(ctx, "oauth2/advertiser/get/", { query: { app_id: cfg.appId, secret: cfg.appSecret } }),
    });
  }

  registerTool(server, ctx, {
    name: "tiktok_ads_list_campaigns",
    title: "TikTok Ads: list campaigns",
    description: "Lists campaigns of a TikTok advertiser with objective, budget and status.",
    input: { advertiser_id: z.string().optional() },
    handler: async (args) => {
      const id = advertiser(cfg, args.advertiser_id);
      const data = await ads<{ list?: any[] }>(ctx, "campaign/get/", { query: { advertiser_id: id, page_size: 1000 } });
      return {
        advertiser_id: id,
        campaigns: (data.list ?? []).map((c) => ({
          id: c.campaign_id,
          name: c.campaign_name,
          objective: c.objective_type,
          status: c.operation_status,
          secondary_status: c.secondary_status,
          budget: c.budget,
          budget_mode: c.budget_mode,
        })),
      };
    },
  });

  registerTool(server, ctx, {
    name: "tiktok_ads_performance",
    title: "TikTok Ads: performance report",
    description: "TikTok Ads report at advertiser, campaign, ad group or ad level: spend, impressions, clicks, conversions, reach and KPIs.",
    input: {
      advertiser_id: z.string().optional(),
      level: z.enum(["advertiser", "campaign", "adgroup", "ad"]).optional().describe("Default campaign."),
      ...dateRangeShape,
    },
    handler: async (args) => {
      const id = advertiser(cfg, args.advertiser_id);
      const range = resolveRange(ctx.now(), args.date_from, args.date_to);
      const level = args.level ?? "campaign";
      const rows = await report(ctx, id, range, level);
      return { advertiser_id: id, level, range, rows: rows.map(withKpis).sort((a, b) => b.spend - a.spend) };
    },
  });

  registerTool(server, ctx, {
    name: "tiktok_ads_set_campaign_status",
    title: "TikTok Ads: pause or enable campaigns",
    description: "Enables or disables (pauses) one or more TikTok campaigns.",
    write: true,
    input: {
      advertiser_id: z.string().optional(),
      campaign_ids: z.array(z.string().regex(/^\d+$/)).min(1).max(20),
      status: z.enum(["ENABLE", "DISABLE"]),
    },
    handler: async (args) => {
      const id = advertiser(cfg, args.advertiser_id);
      return ads(ctx, "campaign/status/update/", {
        body: { advertiser_id: id, campaign_ids: args.campaign_ids, operation_status: args.status },
      });
    },
  });
}

// ---------------------------------------------------------------- TikTok organic (Display API)

async function display<T = any>(ctx: Context, path: string, query: Record<string, string>, body?: unknown): Promise<T> {
  const res = await ctx.http.json<{ data?: T; error?: { code?: string; message?: string } }>(TT, {
    method: body ? "POST" : "GET",
    url: `https://open.tiktokapis.com/v2/${path}`,
    headers: { Authorization: `Bearer ${ctx.config.tiktok!.accessToken}` },
    query,
    body,
  });
  if (res.error && res.error.code && res.error.code !== "ok") {
    throw new Error(`${TT}: API error ${res.error.code} ${res.error.message ?? ""}`.trim());
  }
  return res.data as T;
}

export function registerTikTok(server: McpServer, ctx: Context): void {
  if (!ctx.config.tiktok) return;

  registerTool(server, ctx, {
    name: "tiktok_profile",
    title: "TikTok: profile stats",
    description: "Profile of the authorised TikTok account: display name, followers, following, likes and video count.",
    input: {},
    handler: async () => {
      const data = await display<{ user?: any }>(ctx, "user/info/", {
        fields: "open_id,display_name,bio_description,profile_deep_link,is_verified,follower_count,following_count,likes_count,video_count",
      });
      return data.user ?? data;
    },
  });

  registerTool(server, ctx, {
    name: "tiktok_list_videos",
    title: "TikTok: recent videos",
    description: "Recent public videos of the authorised TikTok account with views, likes, comments and shares.",
    input: {
      max_count: z.number().int().min(1).max(20).optional().describe("Default 20 (API maximum per page)."),
      cursor: z.number().int().optional().describe("Pagination cursor returned by a previous call."),
    },
    handler: async (args) => {
      const data = await display<{ videos?: any[]; cursor?: number; has_more?: boolean }>(
        ctx,
        "video/list/",
        { fields: "id,title,video_description,create_time,share_url,duration,view_count,like_count,comment_count,share_count" },
        { max_count: args.max_count ?? 20, ...(args.cursor !== undefined ? { cursor: args.cursor } : {}) },
      );
      return {
        videos: (data.videos ?? []).map((v) => ({
          ...v,
          create_time: v.create_time ? new Date(v.create_time * 1000).toISOString() : undefined,
          engagement_rate: v.view_count ? Math.round(((num(v.like_count) + num(v.comment_count) + num(v.share_count)) / v.view_count) * 10000) / 100 : null,
        })),
        cursor: data.cursor,
        has_more: data.has_more,
      };
    },
  });
}
