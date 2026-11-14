import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { type AdMetrics, type Context, dateRangeShape, registerTool, resolveRange, round, withKpis } from "../util.js";
import { googleAdsMetrics } from "./googleAds.js";
import { linkedinAdsMetrics } from "./linkedin.js";
import { metaAdsMetrics } from "./meta.js";
import { tiktokAdsMetrics } from "./tiktok.js";

type Fetcher = (ctx: Context, range: { from: string; to: string }, account?: string) => Promise<AdMetrics>;

export const AD_PLATFORMS: Record<string, { label: string; enabled: (ctx: Context) => boolean; fetch: Fetcher }> = {
  google_ads: { label: "Google Ads", enabled: (c) => !!c.config.googleAds, fetch: googleAdsMetrics },
  meta_ads: { label: "Meta Ads", enabled: (c) => !!c.config.meta, fetch: metaAdsMetrics },
  linkedin_ads: { label: "LinkedIn Ads", enabled: (c) => !!c.config.linkedin, fetch: linkedinAdsMetrics },
  tiktok_ads: { label: "TikTok Ads", enabled: (c) => !!c.config.tiktokAds, fetch: tiktokAdsMetrics },
};

export function registerCrossPlatform(server: McpServer, ctx: Context): void {
  const enabled = Object.entries(AD_PLATFORMS).filter(([, p]) => p.enabled(ctx));
  if (enabled.length === 0) return;

  registerTool(server, ctx, {
    name: "ads_cross_platform_summary",
    title: "All ad platforms: blended summary",
    description:
      "Pulls spend, impressions, clicks and conversions from every configured ad platform (Google, Meta, LinkedIn, TikTok) for the same date range, with per-platform KPIs, share of spend and blended totals per currency. Uses the default account of each platform unless overridden.",
    input: {
      ...dateRangeShape,
      platforms: z
        .array(z.enum(["google_ads", "meta_ads", "linkedin_ads", "tiktok_ads"]))
        .optional()
        .describe("Subset of platforms (default: all configured)."),
      accounts: z
        .record(z.string(), z.string())
        .optional()
        .describe('Account override per platform, e.g. {"google_ads":"1234567890","meta_ads":"act_123"}.'),
    },
    handler: async (args) => {
      const range = resolveRange(ctx.now(), args.date_from, args.date_to);
      const selected = enabled.filter(([key]) => !args.platforms || args.platforms.includes(key as any));
      const settled = await Promise.allSettled(selected.map(([key, p]) => p.fetch(ctx, range, args.accounts?.[key])));

      const platforms: ReturnType<typeof withKpis<AdMetrics>>[] = [];
      const errors: { platform: string; error: string }[] = [];
      settled.forEach((r, i) => {
        if (r.status === "fulfilled") platforms.push(withKpis(r.value));
        else errors.push({ platform: selected[i][1].label, error: ctx.http.redact(String(r.reason?.message ?? r.reason)) });
      });

      // Never add up different currencies: totals are grouped by currency.
      const totals: Record<string, AdMetrics> = {};
      for (const p of platforms) {
        const cur = p.currency ?? "unknown";
        totals[cur] ??= { platform: "all", account: "-", currency: cur, spend: 0, impressions: 0, clicks: 0, conversions: 0, conversionValue: 0 };
        const t = totals[cur];
        t.spend += p.spend;
        t.impressions += p.impressions;
        t.clicks += p.clicks;
        t.conversions += p.conversions;
        t.conversionValue! += p.conversionValue ?? 0;
      }
      const blended = Object.values(totals).map((t) => withKpis(t));
      for (const p of platforms) {
        const total = totals[p.currency ?? "unknown"].spend;
        (p as any).share_of_spend_pct = total ? round((p.spend / total) * 100) : null;
      }

      return {
        range,
        platforms,
        blended_by_currency: blended,
        errors,
        notes: [
          "Conversions follow each platform's own attribution model and definition; they overlap and are not deduplicated.",
          "Meta conversions count the 'purchase' action; TikTok reports no conversion value in this summary.",
          "LinkedIn spend is reported in the account's local currency; currency may be unknown when the API omits it.",
        ],
      };
    },
  });
}
