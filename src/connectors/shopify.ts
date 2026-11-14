import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { stripPii } from "../privacy.js";
import { type Context, dateRangeShape, num, registerTool, resolveRange, round } from "../util.js";

const PLATFORM = "Shopify";

async function gql<T = any>(ctx: Context, query: string, variables: Record<string, unknown> = {}): Promise<T> {
  const cfg = ctx.config.shopify!;
  const res = await ctx.http.json<{ data?: T; errors?: any }>(PLATFORM, {
    method: "POST",
    url: `https://${cfg.shopDomain}/admin/api/${cfg.apiVersion}/graphql.json`,
    headers: { "X-Shopify-Access-Token": cfg.accessToken },
    body: { query, variables },
  });
  if (res.errors) {
    const msg = Array.isArray(res.errors) ? res.errors.map((e: any) => e.message).join("; ") : JSON.stringify(res.errors);
    throw new Error(`${PLATFORM}: GraphQL error ${msg}`);
  }
  return res.data as T;
}

/** Returns true when a GraphQL document contains a mutation operation. */
export function isMutation(document: string): boolean {
  const withoutStrings = document.replace(/"""[\s\S]*?"""|"(?:\\.|[^"\\])*"/g, '""').replace(/#.*$/gm, "");
  return /(^|[\s{}])mutation\b/i.test(withoutStrings) || /(^|[\s{}])subscription\b/i.test(withoutStrings);
}

const ORDERS_QUERY = `query Orders($q: String!, $cursor: String) {
  orders(first: 250, after: $cursor, query: $q, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id name createdAt displayFinancialStatus displayFulfillmentStatus cancelledAt
      totalPriceSet { shopMoney { amount currencyCode } }
      totalRefundedSet { shopMoney { amount } }
      sourceName
      customerJourneySummary { firstVisit { source utmParameters { source medium campaign } } }
    }
  }
}`;

export function registerShopify(server: McpServer, ctx: Context): void {
  if (!ctx.config.shopify) return;

  registerTool(server, ctx, {
    name: "shopify_shop_info",
    title: "Shopify: shop info",
    description: "Store name, domain, plan, currency and timezone.",
    input: {},
    handler: async () =>
      gql(ctx, `{ shop { name myshopifyDomain primaryDomain { url } plan { displayName } currencyCode ianaTimezone } }`),
  });

  registerTool(server, ctx, {
    name: "shopify_sales_summary",
    title: "Shopify: sales summary",
    description:
      "Orders, gross sales, refunds, net sales and average order value for a date range, broken down by day, sales channel and first-touch UTM source/campaign. No customer personal data is returned.",
    input: { ...dateRangeShape, max_orders: z.number().int().min(1).max(25000).optional().describe("Safety cap, default 5000.") },
    handler: async (args) => {
      const range = resolveRange(ctx.now(), args.date_from, args.date_to);
      const max = args.max_orders ?? 5000;
      const q = `created_at:>='${range.from}T00:00:00Z' AND created_at:<='${range.to}T23:59:59Z'`;
      const orders: any[] = [];
      let cursor: string | null = null;
      do {
        const data: any = await gql(ctx, ORDERS_QUERY, { q, cursor });
        orders.push(...data.orders.nodes);
        cursor = data.orders.pageInfo.hasNextPage ? data.orders.pageInfo.endCursor : null;
      } while (cursor && orders.length < max);

      let currency: string | undefined;
      let gross = 0;
      let refunds = 0;
      let count = 0;
      let cancelled = 0;
      const byDay: Record<string, { orders: number; sales: number }> = {};
      const byChannel: Record<string, { orders: number; sales: number }> = {};
      const bySource: Record<string, { orders: number; sales: number }> = {};
      const bump = (map: Record<string, { orders: number; sales: number }>, key: string, sales: number) => {
        map[key] ??= { orders: 0, sales: 0 };
        map[key].orders++;
        map[key].sales = round(map[key].sales + sales);
      };

      for (const o of orders.slice(0, max)) {
        if (o.cancelledAt) {
          cancelled++;
          continue;
        }
        const total = num(o.totalPriceSet?.shopMoney?.amount);
        currency = o.totalPriceSet?.shopMoney?.currencyCode ?? currency;
        count++;
        gross += total;
        refunds += num(o.totalRefundedSet?.shopMoney?.amount);
        bump(byDay, String(o.createdAt).slice(0, 10), total);
        bump(byChannel, o.sourceName ?? "unknown", total);
        const utm = o.customerJourneySummary?.firstVisit?.utmParameters;
        const source = utm?.source
          ? `${utm.source}/${utm.medium ?? "-"}${utm.campaign ? ` · ${utm.campaign}` : ""}`
          : (o.customerJourneySummary?.firstVisit?.source ?? "direct/unknown");
        bump(bySource, source, total);
      }

      return {
        range,
        currency,
        orders: count,
        cancelled_orders: cancelled,
        gross_sales: round(gross),
        refunds: round(refunds),
        net_sales: round(gross - refunds),
        average_order_value: count ? round(gross / count) : null,
        truncated: cursor !== null || orders.length > max,
        by_day: byDay,
        by_channel: byChannel,
        by_first_touch_source: bySource,
      };
    },
  });

  registerTool(server, ctx, {
    name: "shopify_top_products",
    title: "Shopify: products",
    description: "Lists products with status, total inventory, price range and vendor; optional search query (Shopify search syntax).",
    input: {
      query: z.string().optional().describe('e.g. "status:active", "title:camiseta*", "inventory_total:<5".'),
      first: z.number().int().min(1).max(250).optional().describe("Default 50."),
    },
    handler: async (args) => {
      const data = await gql(
        ctx,
        `query P($first: Int!, $q: String) { products(first: $first, query: $q, sortKey: UPDATED_AT, reverse: true) { nodes {
          id title handle status vendor productType totalInventory onlineStoreUrl
          priceRangeV2 { minVariantPrice { amount currencyCode } maxVariantPrice { amount } }
        } } }`,
        { first: args.first ?? 50, q: args.query ?? null },
      );
      return { products: data.products.nodes };
    },
  });

  registerTool(server, ctx, {
    name: "shopify_graphql_query",
    title: "Shopify: read-only GraphQL query",
    description:
      "Runs a read-only Shopify Admin GraphQL query (mutations are rejected). Customer personal data (names, emails, phones, addresses, IPs) is stripped from the result.",
    input: {
      query: z.string().min(3),
      variables: z.record(z.string(), z.unknown()).optional(),
    },
    handler: async (args) => {
      if (isMutation(args.query)) throw new Error("Mutations and subscriptions are not allowed in shopify_graphql_query.");
      return stripPii(await gql(ctx, args.query, args.variables ?? {}));
    },
  });
}
