import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { MetaConfig } from "../config.js";
import { type AdMetrics, type Context, dateRangeShape, num, registerTool, requireValue, resolveRange, withKpis } from "../util.js";

const ADS = "Meta Ads";
const FB = "Facebook";
const IG = "Instagram";

function base(cfg: MetaConfig): string {
  return `https://graph.facebook.com/${cfg.apiVersion}`;
}

/** Page access tokens never leave the process: they are cached here and stripped from tool output. */
const pageTokens = new Map<string, string>();

export function resetMetaCache(): void {
  pageTokens.clear();
}

async function graph<T = any>(
  ctx: Context,
  platform: string,
  path: string,
  query: Record<string, string | number | boolean | undefined> = {},
  token?: string,
): Promise<T> {
  const cfg = ctx.config.meta!;
  return ctx.http.json<T>(platform, {
    url: path.startsWith("https://") ? path : `${base(cfg)}/${path.replace(/^\//, "")}`,
    headers: { Authorization: `Bearer ${token ?? cfg.accessToken}` },
    query,
  });
}

async function graphPost<T = any>(ctx: Context, platform: string, path: string, form: Record<string, string>, token?: string): Promise<T> {
  const cfg = ctx.config.meta!;
  return ctx.http.json<T>(platform, {
    method: "POST",
    url: `${base(cfg)}/${path.replace(/^\//, "")}`,
    headers: { Authorization: `Bearer ${token ?? cfg.accessToken}` },
    form,
  });
}

/** Follows Graph API cursor pagination up to `max` items. */
async function graphAll(
  ctx: Context,
  platform: string,
  path: string,
  query: Record<string, string | number | boolean | undefined>,
  max = 500,
  token?: string,
): Promise<any[]> {
  const items: any[] = [];
  let res = await graph<{ data?: any[]; paging?: { next?: string } }>(ctx, platform, path, query, token);
  items.push(...(res.data ?? []));
  while (res.paging?.next && items.length < max) {
    res = await graph(ctx, platform, res.paging.next, {}, token);
    items.push(...(res.data ?? []));
  }
  return items.slice(0, max);
}

function stripTokens<T>(value: T): T {
  if (Array.isArray(value)) return value.map(stripTokens) as T;
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) {
      if (k === "access_token") continue;
      out[k] = stripTokens(v);
    }
    return out as T;
  }
  return value;
}

async function pageToken(ctx: Context, pageId: string): Promise<string> {
  const cached = pageTokens.get(pageId);
  if (cached) return cached;
  const res = await graph<{ access_token?: string }>(ctx, FB, pageId, { fields: "access_token" });
  if (!res.access_token) {
    throw new Error(`No page access token for page ${pageId}. The token needs pages_show_list / pages_read_engagement and a Page role.`);
  }
  pageTokens.set(pageId, res.access_token);
  return res.access_token;
}

function adAccount(cfg: MetaConfig, value?: string): string {
  const id = requireValue(value ?? cfg.defaultAdAccountId, "ad_account_id (META_AD_ACCOUNT_ID)").replace(/^act_/, "");
  if (!/^\d+$/.test(id)) throw new Error("ad_account_id must be numeric (with or without the act_ prefix).");
  return `act_${id}`;
}

function sumActions(actions: any[] | undefined, types: string[]): number {
  return (actions ?? []).filter((a) => types.includes(a.action_type)).reduce((acc, a) => acc + num(a.value), 0);
}

function actionMap(actions: any[] | undefined): Record<string, number> {
  return Object.fromEntries((actions ?? []).map((a) => [a.action_type, num(a.value)]));
}

const DEFAULT_CONVERSIONS = ["purchase"];

const conversionShape = {
  conversion_action_types: z
    .array(z.string())
    .optional()
    .describe('Meta action_type values counted as conversions (default ["purchase"]; e.g. ["lead"] for lead generation).'),
};

export async function metaAdsMetrics(ctx: Context, range: { from: string; to: string }, account?: string): Promise<AdMetrics> {
  const cfg = ctx.config.meta!;
  const act = adAccount(cfg, account);
  const rows = await graphAll(ctx, ADS, `${act}/insights`, {
    level: "account",
    fields: "spend,impressions,clicks,actions,action_values,account_currency",
    time_range: JSON.stringify({ since: range.from, until: range.to }),
  });
  const m: AdMetrics = { platform: ADS, account: act, spend: 0, impressions: 0, clicks: 0, conversions: 0, conversionValue: 0 };
  for (const r of rows) {
    m.currency = r.account_currency ?? m.currency;
    m.spend += num(r.spend);
    m.impressions += num(r.impressions);
    m.clicks += num(r.clicks);
    m.conversions += sumActions(r.actions, DEFAULT_CONVERSIONS);
    m.conversionValue! += sumActions(r.action_values, DEFAULT_CONVERSIONS);
  }
  return m;
}

export function registerMeta(server: McpServer, ctx: Context): void {
  const cfg = ctx.config.meta;
  if (!cfg) return;

  // ---------------------------------------------------------------- Meta Ads
  registerTool(server, ctx, {
    name: "meta_ads_list_accounts",
    title: "Meta Ads: list ad accounts",
    description: "Lists the Meta (Facebook/Instagram) ad accounts available to the token.",
    input: {},
    handler: async () => ({
      accounts: await graphAll(ctx, ADS, "me/adaccounts", {
        fields: "id,name,account_status,currency,timezone_name,amount_spent",
        limit: 100,
      }),
    }),
  });

  registerTool(server, ctx, {
    name: "meta_ads_list_campaigns",
    title: "Meta Ads: list campaigns",
    description: "Lists campaigns of a Meta ad account with status, objective and budgets.",
    input: {
      ad_account_id: z.string().optional(),
      effective_status: z.array(z.string()).optional().describe('e.g. ["ACTIVE","PAUSED"]'),
    },
    handler: async (args) => {
      const act = adAccount(cfg, args.ad_account_id);
      return {
        ad_account_id: act,
        campaigns: await graphAll(ctx, ADS, `${act}/campaigns`, {
          fields: "id,name,status,effective_status,objective,daily_budget,lifetime_budget,start_time,stop_time",
          effective_status: args.effective_status ? JSON.stringify(args.effective_status) : undefined,
          limit: 200,
        }),
      };
    },
  });

  registerTool(server, ctx, {
    name: "meta_ads_performance",
    title: "Meta Ads: performance report",
    description:
      "Insights for a Meta ad account at account, campaign, adset or ad level: spend, impressions, clicks, reach, frequency, conversions, CTR, CPC, CPA, ROAS and all action counts.",
    input: {
      ad_account_id: z.string().optional(),
      level: z.enum(["account", "campaign", "adset", "ad"]).optional().describe("Default campaign."),
      ...dateRangeShape,
      ...conversionShape,
      breakdowns: z.string().optional().describe('Optional breakdowns, e.g. "age,gender" or "publisher_platform".'),
    },
    handler: async (args) => {
      const act = adAccount(cfg, args.ad_account_id);
      const range = resolveRange(ctx.now(), args.date_from, args.date_to);
      const level = args.level ?? "campaign";
      const types = args.conversion_action_types ?? DEFAULT_CONVERSIONS;
      const rows = await graphAll(ctx, ADS, `${act}/insights`, {
        level,
        fields:
          "campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,spend,impressions,clicks,reach,frequency,actions,action_values,account_currency",
        time_range: JSON.stringify({ since: range.from, until: range.to }),
        breakdowns: args.breakdowns,
        limit: 500,
      });
      return {
        ad_account_id: act,
        level,
        range,
        conversion_action_types: types,
        rows: rows.map((r) => {
          const { actions, action_values, ...rest } = r;
          return {
            ...withKpis({
              ...rest,
              spend: num(r.spend),
              impressions: num(r.impressions),
              clicks: num(r.clicks),
              conversions: sumActions(actions, types),
              conversionValue: sumActions(action_values, types),
            }),
            actions: actionMap(actions),
          };
        }),
      };
    },
  });

  registerTool(server, ctx, {
    name: "meta_ads_set_status",
    title: "Meta Ads: pause or activate",
    description: "Sets the status of a Meta campaign, ad set or ad (by object ID) to ACTIVE or PAUSED.",
    write: true,
    input: {
      object_id: z.string().regex(/^\d+$/).describe("Campaign, ad set or ad ID."),
      status: z.enum(["ACTIVE", "PAUSED"]),
    },
    handler: async (args) => graphPost(ctx, ADS, args.object_id, { status: args.status }),
  });

  // ---------------------------------------------------------------- Facebook Pages
  registerTool(server, ctx, {
    name: "facebook_list_pages",
    title: "Facebook: list pages",
    description: "Lists the Facebook Pages the token manages, with followers and the linked Instagram business account.",
    input: {},
    handler: async () => {
      const pages = await graphAll(ctx, FB, "me/accounts", {
        fields: "id,name,category,fan_count,followers_count,link,access_token,instagram_business_account{id,username}",
        limit: 100,
      });
      for (const p of pages) if (p.id && p.access_token) pageTokens.set(p.id, p.access_token);
      return { pages: stripTokens(pages) };
    },
  });

  const pageShape = { page_id: z.string().optional().describe("Facebook Page ID (default META_PAGE_ID).") };
  const pageId = (v?: string) => requireValue(v ?? cfg.defaultPageId, "page_id (META_PAGE_ID)");

  registerTool(server, ctx, {
    name: "facebook_page_posts",
    title: "Facebook: page posts",
    description: "Recent posts of a Facebook Page with reactions, comments and shares counts.",
    input: { ...pageShape, limit: z.number().int().min(1).max(100).optional().describe("Default 25.") },
    handler: async (args) => {
      const id = pageId(args.page_id);
      const posts = await graphAll(
        ctx,
        FB,
        `${id}/posts`,
        {
          fields:
            "id,message,created_time,permalink_url,status_type,shares,reactions.summary(total_count).limit(0),comments.summary(total_count).limit(0)",
          limit: args.limit ?? 25,
        },
        args.limit ?? 25,
        await pageToken(ctx, id),
      );
      return {
        page_id: id,
        posts: posts.map((p) => ({
          id: p.id,
          created_time: p.created_time,
          message: p.message,
          permalink_url: p.permalink_url,
          type: p.status_type,
          reactions: num(p.reactions?.summary?.total_count),
          comments: num(p.comments?.summary?.total_count),
          shares: num(p.shares?.count),
        })),
      };
    },
  });

  registerTool(server, ctx, {
    name: "facebook_page_insights",
    title: "Facebook: page insights",
    description:
      "Daily Facebook Page insights for a date range. Metric names change between Graph API versions; pass `metrics` explicitly if a default is rejected.",
    input: {
      ...pageShape,
      ...dateRangeShape,
      metrics: z.array(z.string()).optional().describe('Default ["page_impressions_unique","page_post_engagements","page_follows"].'),
    },
    handler: async (args) => {
      const id = pageId(args.page_id);
      const range = resolveRange(ctx.now(), args.date_from, args.date_to);
      const metrics = args.metrics ?? ["page_impressions_unique", "page_post_engagements", "page_follows"];
      const res = await graph(
        ctx,
        FB,
        `${id}/insights`,
        { metric: metrics.join(","), period: "day", since: range.from, until: range.to },
        await pageToken(ctx, id),
      );
      return {
        page_id: id,
        range,
        metrics: (res.data ?? []).map((m: any) => ({
          name: m.name,
          title: m.title,
          total: (m.values ?? []).reduce((acc: number, v: any) => acc + (typeof v.value === "number" ? v.value : 0), 0),
          values: m.values,
        })),
      };
    },
  });

  registerTool(server, ctx, {
    name: "facebook_publish_post",
    title: "Facebook: publish or schedule a post",
    description: "Publishes a text/link post on a Facebook Page, or schedules it (10 minutes to 30 days ahead).",
    write: true,
    input: {
      ...pageShape,
      message: z.string().min(1).max(63206),
      link: z.string().url().optional(),
      scheduled_publish_time: z.string().datetime({ offset: true }).optional().describe("ISO 8601 date-time to schedule the post."),
    },
    handler: async (args) => {
      const id = pageId(args.page_id);
      const form: Record<string, string> = { message: args.message };
      if (args.link) form.link = args.link;
      if (args.scheduled_publish_time) {
        form.published = "false";
        form.scheduled_publish_time = String(Math.floor(Date.parse(args.scheduled_publish_time) / 1000));
      }
      return graphPost(ctx, FB, `${id}/feed`, form, await pageToken(ctx, id));
    },
  });

  // ---------------------------------------------------------------- Instagram
  const igShape = { instagram_account_id: z.string().optional().describe("Instagram business account ID (default META_INSTAGRAM_ACCOUNT_ID).") };
  const igId = (v?: string) => requireValue(v ?? cfg.defaultInstagramAccountId, "instagram_account_id (META_INSTAGRAM_ACCOUNT_ID)");

  registerTool(server, ctx, {
    name: "instagram_account_info",
    title: "Instagram: account info",
    description: "Profile data of an Instagram professional account: username, followers, follows and media count.",
    input: igShape,
    handler: async (args) =>
      graph(ctx, IG, igId(args.instagram_account_id), {
        fields: "id,username,name,biography,website,followers_count,follows_count,media_count",
      }),
  });

  registerTool(server, ctx, {
    name: "instagram_list_media",
    title: "Instagram: recent media",
    description: "Recent Instagram posts, reels and carousels with likes and comment counts.",
    input: { ...igShape, limit: z.number().int().min(1).max(100).optional().describe("Default 25.") },
    handler: async (args) => ({
      media: await graphAll(
        ctx,
        IG,
        `${igId(args.instagram_account_id)}/media`,
        {
          fields: "id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count",
          limit: args.limit ?? 25,
        },
        args.limit ?? 25,
      ),
    }),
  });

  registerTool(server, ctx, {
    name: "instagram_media_insights",
    title: "Instagram: media insights",
    description: "Insights for one Instagram media object (reach, views, saves, shares, total interactions).",
    input: {
      media_id: z.string().regex(/^\d+$/),
      metrics: z.array(z.string()).optional().describe('Default ["reach","views","saved","shares","total_interactions"].'),
    },
    handler: async (args) => {
      const metrics = args.metrics ?? ["reach", "views", "saved", "shares", "total_interactions"];
      const res = await graph(ctx, IG, `${args.media_id}/insights`, { metric: metrics.join(",") });
      return {
        media_id: args.media_id,
        metrics: Object.fromEntries((res.data ?? []).map((m: any) => [m.name, m.values?.[0]?.value ?? m.total_value?.value ?? null])),
      };
    },
  });

  registerTool(server, ctx, {
    name: "instagram_account_insights",
    title: "Instagram: account insights",
    description: "Account-level Instagram insights (total values) for a date range of up to 30 days.",
    input: {
      ...igShape,
      ...dateRangeShape,
      metrics: z
        .array(z.string())
        .optional()
        .describe('Default ["reach","views","accounts_engaged","total_interactions","profile_links_taps"].'),
    },
    handler: async (args) => {
      const range = resolveRange(ctx.now(), args.date_from, args.date_to);
      const metrics = args.metrics ?? ["reach", "views", "accounts_engaged", "total_interactions", "profile_links_taps"];
      const res = await graph(ctx, IG, `${igId(args.instagram_account_id)}/insights`, {
        metric: metrics.join(","),
        period: "day",
        metric_type: "total_value",
        since: Math.floor(Date.parse(`${range.from}T00:00:00Z`) / 1000),
        until: Math.floor(Date.parse(`${range.to}T23:59:59Z`) / 1000),
      });
      return {
        range,
        metrics: Object.fromEntries((res.data ?? []).map((m: any) => [m.name, m.total_value?.value ?? null])),
      };
    },
  });

  registerTool(server, ctx, {
    name: "instagram_publish",
    title: "Instagram: publish image or reel",
    description: "Publishes an image or a reel to an Instagram professional account from a public media URL.",
    write: true,
    input: {
      ...igShape,
      media_type: z.enum(["IMAGE", "REELS"]),
      media_url: z.string().url().describe("Publicly reachable JPEG (image) or MP4 (reel) URL."),
      caption: z.string().max(2200).optional(),
    },
    handler: async (args) => {
      const id = igId(args.instagram_account_id);
      const form: Record<string, string> = args.media_type === "IMAGE" ? { image_url: args.media_url } : { media_type: "REELS", video_url: args.media_url };
      if (args.caption) form.caption = args.caption;
      const container = await graphPost<{ id: string }>(ctx, IG, `${id}/media`, form);
      if (args.media_type === "REELS") {
        let ready = false;
        for (let i = 0; i < 20 && !ready; i++) {
          const status = await graph<{ status_code?: string }>(ctx, IG, container.id, { fields: "status_code" });
          if (status.status_code === "FINISHED") {
            ready = true;
            break;
          }
          if (status.status_code === "ERROR" || status.status_code === "EXPIRED") {
            throw new Error(`Instagram could not process the video (status ${status.status_code}).`);
          }
          await new Promise((r) => setTimeout(r, 3000));
        }
        if (!ready) {
          return { container_id: container.id, status: "processing", note: "Instagram was still processing the video after 60 s, so it was not published. Retry later or use a shorter video." };
        }
      }
      const published = await graphPost<{ id: string }>(ctx, IG, `${id}/media_publish`, { creation_id: container.id });
      return { container_id: container.id, media_id: published.id };
    },
  });
}
