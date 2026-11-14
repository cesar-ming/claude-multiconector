import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { LinkedInConfig } from "../config.js";
import { type AdMetrics, type Context, dateRangeShape, num, registerTool, requireValue, resolveRange, withKpis } from "../util.js";

const ADS = "LinkedIn Ads";
const LI = "LinkedIn";
const BASE = "https://api.linkedin.com/rest";

function headers(cfg: LinkedInConfig, extra: Record<string, string> = {}): Record<string, string> {
  return {
    Authorization: `Bearer ${cfg.accessToken}`,
    "LinkedIn-Version": cfg.apiVersion,
    "X-Restli-Protocol-Version": "2.0.0",
    ...extra,
  };
}

/**
 * Rest.li 2.0 query strings need raw parentheses/commas but encoded URNs,
 * so URLs are built by hand instead of with URLSearchParams.
 */
function urn(type: string, id: string): string {
  return encodeURIComponent(`urn:li:${type}:${id}`);
}

function restliDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return `(year:${y},month:${m},day:${d})`;
}

async function get<T = any>(ctx: Context, platform: string, pathAndQuery: string): Promise<T> {
  return ctx.http.json<T>(platform, { url: `${BASE}/${pathAndQuery}`, headers: headers(ctx.config.linkedin!) });
}

function adAccount(cfg: LinkedInConfig, v?: string): string {
  const id = requireValue(v?.replace(/\D/g, "") || cfg.defaultAdAccountId, "ad_account_id (LINKEDIN_AD_ACCOUNT_ID)");
  return id;
}

function organization(cfg: LinkedInConfig, v?: string): string {
  return requireValue(v?.replace(/\D/g, "") || cfg.defaultOrganizationId, "organization_id (LINKEDIN_ORGANIZATION_ID)");
}

const ANALYTICS_FIELDS = "pivotValues,costInLocalCurrency,impressions,clicks,externalWebsiteConversions,oneClickLeads,conversionValueInLocalCurrency";

async function analytics(ctx: Context, account: string, range: { from: string; to: string }, pivot: string): Promise<any[]> {
  const res = await get<{ elements?: any[] }>(
    ctx,
    ADS,
    `adAnalytics?q=analytics&pivot=${pivot}&timeGranularity=ALL` +
      `&dateRange=(start:${restliDate(range.from)},end:${restliDate(range.to)})` +
      `&accounts=List(${urn("sponsoredAccount", account)})&fields=${ANALYTICS_FIELDS}`,
  );
  return res.elements ?? [];
}

function toMetrics(e: any) {
  return {
    spend: num(e.costInLocalCurrency),
    impressions: num(e.impressions),
    clicks: num(e.clicks),
    conversions: num(e.externalWebsiteConversions) + num(e.oneClickLeads),
    conversionValue: num(e.conversionValueInLocalCurrency),
    leads: num(e.oneClickLeads),
  };
}

export async function linkedinAdsMetrics(ctx: Context, range: { from: string; to: string }, account?: string): Promise<AdMetrics> {
  const id = adAccount(ctx.config.linkedin!, account);
  const rows = await analytics(ctx, id, range, "ACCOUNT");
  const m: AdMetrics = { platform: ADS, account: id, spend: 0, impressions: 0, clicks: 0, conversions: 0, conversionValue: 0 };
  for (const r of rows) {
    const x = toMetrics(r);
    m.spend += x.spend;
    m.impressions += x.impressions;
    m.clicks += x.clicks;
    m.conversions += x.conversions;
    m.conversionValue! += x.conversionValue;
  }
  return m;
}

export function registerLinkedIn(server: McpServer, ctx: Context): void {
  const cfg = ctx.config.linkedin;
  if (!cfg) return;

  // ---------------------------------------------------------------- LinkedIn Ads
  registerTool(server, ctx, {
    name: "linkedin_ads_list_accounts",
    title: "LinkedIn Ads: list ad accounts",
    description: "Lists LinkedIn ad accounts the member can access.",
    input: {},
    handler: async () => {
      const res = await get(ctx, ADS, "adAccounts?q=search");
      return {
        accounts: (res.elements ?? []).map((a: any) => ({ id: a.id, name: a.name, currency: a.currency, status: a.status, type: a.type })),
      };
    },
  });

  registerTool(server, ctx, {
    name: "linkedin_ads_list_campaigns",
    title: "LinkedIn Ads: list campaigns",
    description: "Lists campaigns of a LinkedIn ad account with status, objective and budgets.",
    input: { ad_account_id: z.string().optional() },
    handler: async (args) => {
      const id = adAccount(cfg, args.ad_account_id);
      const res = await get(ctx, ADS, `adAccounts/${id}/adCampaigns?q=search`);
      return {
        ad_account_id: id,
        campaigns: (res.elements ?? []).map((c: any) => ({
          id: c.id,
          name: c.name,
          status: c.status,
          objective: c.objectiveType,
          type: c.type,
          daily_budget: c.dailyBudget,
          total_budget: c.totalBudget,
          cost_type: c.costType,
        })),
      };
    },
  });

  registerTool(server, ctx, {
    name: "linkedin_ads_performance",
    title: "LinkedIn Ads: performance report",
    description: "LinkedIn ad analytics per campaign (or account / creative): spend, impressions, clicks, conversions, leads and KPIs.",
    input: {
      ad_account_id: z.string().optional(),
      pivot: z.enum(["ACCOUNT", "CAMPAIGN_GROUP", "CAMPAIGN", "CREATIVE"]).optional().describe("Default CAMPAIGN."),
      ...dateRangeShape,
    },
    handler: async (args) => {
      const id = adAccount(cfg, args.ad_account_id);
      const range = resolveRange(ctx.now(), args.date_from, args.date_to);
      const pivot = args.pivot ?? "CAMPAIGN";
      const rows = await analytics(ctx, id, range, pivot);
      return {
        ad_account_id: id,
        pivot,
        range,
        rows: rows
          .map((r) => withKpis({ entity: r.pivotValues?.[0], ...toMetrics(r) }))
          .sort((a, b) => b.spend - a.spend),
      };
    },
  });

  registerTool(server, ctx, {
    name: "linkedin_ads_set_campaign_status",
    title: "LinkedIn Ads: pause or activate a campaign",
    description: "Sets a LinkedIn campaign status to ACTIVE or PAUSED.",
    write: true,
    input: {
      ad_account_id: z.string().optional(),
      campaign_id: z.string().regex(/^\d+$/),
      status: z.enum(["ACTIVE", "PAUSED"]),
    },
    handler: async (args) => {
      const id = adAccount(cfg, args.ad_account_id);
      await ctx.http.json(ADS, {
        method: "POST",
        url: `${BASE}/adAccounts/${id}/adCampaigns/${args.campaign_id}`,
        headers: headers(cfg, { "X-RestLi-Method": "PARTIAL_UPDATE" }),
        body: { patch: { $set: { status: args.status } } },
      });
      return { campaign_id: args.campaign_id, status: args.status, updated: true };
    },
  });

  // ---------------------------------------------------------------- LinkedIn organic (company pages)
  const orgShape = { organization_id: z.string().optional().describe("LinkedIn organization (company page) ID.") };

  registerTool(server, ctx, {
    name: "linkedin_organization_overview",
    title: "LinkedIn: company page overview",
    description: "Company page name, vanity URL, follower count and lifetime share statistics (impressions, clicks, engagement).",
    input: orgShape,
    handler: async (args) => {
      const id = organization(cfg, args.organization_id);
      const [org, followers, stats] = await Promise.all([
        get(ctx, LI, `organizations/${id}`),
        get(ctx, LI, `networkSizes/${urn("organization", id)}?edgeType=COMPANY_FOLLOWED_BY_MEMBER`).catch(() => ({})),
        get(ctx, LI, `organizationalEntityShareStatistics?q=organizationalEntity&organizationalEntity=${urn("organization", id)}`).catch(
          () => ({}),
        ),
      ]);
      return {
        id,
        name: org.localizedName,
        vanity_name: org.vanityName,
        followers: followers.firstDegreeSize ?? null,
        share_statistics: stats.elements?.[0]?.totalShareStatistics ?? null,
      };
    },
  });

  registerTool(server, ctx, {
    name: "linkedin_organization_posts",
    title: "LinkedIn: company page posts",
    description: "Recent posts published by a LinkedIn company page.",
    input: { ...orgShape, count: z.number().int().min(1).max(100).optional().describe("Default 20.") },
    handler: async (args) => {
      const id = organization(cfg, args.organization_id);
      const res = await get(ctx, LI, `posts?q=author&author=${urn("organization", id)}&count=${args.count ?? 20}&sortBy=LAST_MODIFIED`);
      return {
        organization_id: id,
        posts: (res.elements ?? []).map((p: any) => ({
          id: p.id,
          commentary: p.commentary,
          lifecycle_state: p.lifecycleState,
          published_at: p.publishedAt ? new Date(p.publishedAt).toISOString() : null,
          visibility: p.visibility,
        })),
      };
    },
  });

  registerTool(server, ctx, {
    name: "linkedin_publish_post",
    title: "LinkedIn: publish a company page post",
    description: "Publishes a text post (optionally with an article link) on a LinkedIn company page.",
    write: true,
    input: {
      ...orgShape,
      commentary: z.string().min(1).max(3000),
      article_url: z.string().url().optional(),
      article_title: z.string().max(200).optional(),
    },
    handler: async (args) => {
      const id = organization(cfg, args.organization_id);
      const body: Record<string, unknown> = {
        author: `urn:li:organization:${id}`,
        commentary: args.commentary,
        visibility: "PUBLIC",
        distribution: { feedDistribution: "MAIN_FEED", targetEntities: [], thirdPartyDistributionChannels: [] },
        lifecycleState: "PUBLISHED",
        isReshareDisabledByAuthor: false,
      };
      if (args.article_url) {
        body.content = { article: { source: args.article_url, title: args.article_title ?? args.article_url } };
      }
      await ctx.http.json(LI, { method: "POST", url: `${BASE}/posts`, headers: headers(cfg), body });
      return { organization_id: id, published: true };
    },
  });
}
