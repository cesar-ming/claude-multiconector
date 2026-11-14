import { describe, expect, it } from "vitest";
import { ALL_ENV, connect, type Call } from "./helpers.js";

const googleToken = (c: Call) => (c.url === "https://oauth2.googleapis.com/token" ? { access_token: "g-access", expires_in: 3600 } : undefined);

describe("Google Ads", () => {
  it("exchanges the refresh token and normalises campaign metrics", async () => {
    const { call, calls } = await connect(ALL_ENV, [
      googleToken,
      (c) =>
        c.url.endsWith("/customers/1234567890/googleAds:search")
          ? { results: [{ campaign: { id: "1", name: "Brand", status: "ENABLED" }, metrics: { costMicros: "12500000", impressions: "1000", clicks: "50", conversions: 5, conversionsValue: 250 } }] }
          : undefined,
    ]);
    const r = await call("google_ads_campaign_performance", { date_from: "2026-11-01", date_to: "2026-11-07" });
    expect(r.isError).toBe(false);
    expect(r.json.campaigns[0]).toMatchObject({ name: "Brand", spend: 12.5, ctr: 5, cpc: 0.25, cpa: 2.5, roas: 20 });
    const search = calls.find((c) => c.url.includes("googleAds:search"))!;
    expect(search.headers["developer-token"]).toBe("dev-token-123");
    expect(search.headers["login-customer-id"]).toBe("9990001111");
    expect(search.headers.Authorization).toBe("Bearer g-access");
    expect(JSON.parse(search.body!).query).toContain("BETWEEN '2026-11-01' AND '2026-11-07'");
  });

  it("rejects non-SELECT GAQL", async () => {
    const { call, calls } = await connect(ALL_ENV, [googleToken]);
    const r = await call("google_ads_query", { query: "DELETE FROM campaign WHERE 1=1" });
    expect(r.isError).toBe(true);
    expect(calls).toHaveLength(0);
  });
});

describe("Meta", () => {
  it("counts purchase conversions and exposes all actions", async () => {
    const { call, calls } = await connect(ALL_ENV, [
      (c) =>
        c.url.includes("/act_555/insights")
          ? {
              data: [
                {
                  campaign_name: "Prospecting",
                  spend: "100",
                  impressions: "20000",
                  clicks: "400",
                  actions: [{ action_type: "purchase", value: "8" }, { action_type: "link_click", value: "390" }],
                  action_values: [{ action_type: "purchase", value: "640" }],
                },
              ],
            }
          : undefined,
    ]);
    const r = await call("meta_ads_performance", { date_from: "2026-11-01", date_to: "2026-11-07" });
    expect(r.json.rows[0]).toMatchObject({ campaign_name: "Prospecting", conversions: 8, cpa: 12.5, roas: 6.4, actions: { link_click: 390 } });
    expect(new URL(calls[0].url).searchParams.get("time_range")).toBe('{"since":"2026-11-01","until":"2026-11-07"}');
    expect(calls[0].url).not.toContain("meta-secret-token");
  });

  it("never returns page access tokens and uses them for page calls", async () => {
    const { call, calls } = await connect(ALL_ENV, [
      (c) => (c.url.includes("/me/accounts") ? { data: [{ id: "777", name: "Mi Página", access_token: "PAGE-TOKEN-SECRET" }] } : undefined),
      (c) => (c.url.includes("/777/posts") ? { data: [{ id: "777_1", message: "Hola", reactions: { summary: { total_count: 3 } }, comments: { summary: { total_count: 1 } } }] } : undefined),
    ]);
    const pages = await call("facebook_list_pages");
    expect(pages.text).not.toContain("PAGE-TOKEN-SECRET");
    const posts = await call("facebook_page_posts");
    expect(posts.json.posts[0]).toMatchObject({ reactions: 3, comments: 1, shares: 0 });
    expect(calls.at(-1)!.headers.Authorization).toBe("Bearer PAGE-TOKEN-SECRET");
  });

  it("publishes an Instagram image in two steps when writes are allowed", async () => {
    const { call, calls } = await connect({ ...ALL_ENV, MCP_ALLOW_WRITES: "1" }, [
      (c) => (c.url.endsWith("/888/media") && c.method === "POST" ? { id: "c1" } : undefined),
      (c) => (c.url.endsWith("/888/media_publish") ? { id: "m1" } : undefined),
    ]);
    const r = await call("instagram_publish", { media_type: "IMAGE", media_url: "https://cdn.example.com/a.jpg", caption: "Hola", confirm: true });
    expect(r.json).toEqual({ container_id: "c1", media_id: "m1" });
    expect(calls[1].body).toBe("creation_id=c1");
  });
});

describe("LinkedIn", () => {
  it("builds Rest.li analytics queries with versioned headers", async () => {
    const { call, calls } = await connect(ALL_ENV, [
      (c) =>
        c.url.includes("/rest/adAnalytics")
          ? { elements: [{ pivotValues: ["urn:li:sponsoredCampaign:1"], costInLocalCurrency: "50.5", impressions: 1000, clicks: 10, externalWebsiteConversions: 1, oneClickLeads: 2 }] }
          : undefined,
    ]);
    const r = await call("linkedin_ads_performance", { date_from: "2026-11-01", date_to: "2026-11-07" });
    expect(r.json.rows[0]).toMatchObject({ entity: "urn:li:sponsoredCampaign:1", spend: 50.5, conversions: 3, leads: 2 });
    expect(calls[0].url).toContain("dateRange=(start:(year:2026,month:11,day:1),end:(year:2026,month:11,day:7))");
    expect(calls[0].url).toContain("accounts=List(urn%3Ali%3AsponsoredAccount%3A5001)");
    expect(calls[0].headers["LinkedIn-Version"]).toBe("202510");
  });
});

describe("TikTok", () => {
  it("raises business API errors", async () => {
    const { call } = await connect(ALL_ENV, [(c) => (c.url.includes("business-api.tiktok.com") ? { code: 40001, message: "Access token is incorrect" } : undefined)]);
    const r = await call("tiktok_ads_list_campaigns");
    expect(r.isError).toBe(true);
    expect(r.text).toContain("40001");
  });

  it("reports campaign metrics", async () => {
    const { call, calls } = await connect(ALL_ENV, [
      (c) =>
        c.url.includes("report/integrated/get")
          ? { code: 0, data: { list: [{ dimensions: { campaign_id: "9" }, metrics: { campaign_name: "Spark", spend: "30", impressions: "3000", clicks: "60", conversion: "3", reach: "2000" } }], page_info: { total_page: 1 } } }
          : undefined,
    ]);
    const r = await call("tiktok_ads_performance", {});
    expect(r.json.rows[0]).toMatchObject({ id: "9", name: "Spark", spend: 30, cpa: 10 });
    expect(calls[0].headers["Access-Token"]).toBe("tt-ads-secret");
  });

  it("lists organic videos with engagement rate", async () => {
    const { call } = await connect(ALL_ENV, [
      (c) => (c.url.includes("open.tiktokapis.com/v2/video/list") ? { data: { videos: [{ id: "v", view_count: 1000, like_count: 80, comment_count: 10, share_count: 10, create_time: 1763100000 }], has_more: false }, error: { code: "ok" } } : undefined),
    ]);
    const r = await call("tiktok_list_videos");
    expect(r.json.videos[0].engagement_rate).toBe(10);
  });
});

describe("Shopify", () => {
  it("summarises sales without customer data", async () => {
    const { call, calls } = await connect(ALL_ENV, [
      (c) =>
        c.url === "https://demo.myshopify.com/admin/api/2026-10/graphql.json"
          ? {
              data: {
                orders: {
                  pageInfo: { hasNextPage: false, endCursor: null },
                  nodes: [
                    { createdAt: "2026-11-01T10:00:00Z", totalPriceSet: { shopMoney: { amount: "100.00", currencyCode: "EUR" } }, totalRefundedSet: { shopMoney: { amount: "10" } }, sourceName: "web", customerJourneySummary: { firstVisit: { utmParameters: { source: "google", medium: "cpc", campaign: "brand" } } } },
                    { createdAt: "2026-11-02T10:00:00Z", totalPriceSet: { shopMoney: { amount: "50.00", currencyCode: "EUR" } }, totalRefundedSet: { shopMoney: { amount: "0" } }, sourceName: "web" },
                    { createdAt: "2026-11-02T11:00:00Z", cancelledAt: "2026-11-02T12:00:00Z", totalPriceSet: { shopMoney: { amount: "999" } } },
                  ],
                },
              },
            }
          : undefined,
    ]);
    const r = await call("shopify_sales_summary", { date_from: "2026-11-01", date_to: "2026-11-07" });
    expect(r.json).toMatchObject({ orders: 2, cancelled_orders: 1, gross_sales: 150, refunds: 10, net_sales: 140, average_order_value: 75, currency: "EUR", truncated: false });
    expect(r.json.by_first_touch_source["google/cpc · brand"]).toEqual({ orders: 1, sales: 100 });
    expect(calls[0].headers["X-Shopify-Access-Token"]).toBe("shpat_secret_token");
    expect(calls[0].body).not.toMatch(/email|phone|address/i);
  });

  it("rejects mutations in the generic query tool", async () => {
    const { call, calls } = await connect(ALL_ENV);
    const r = await call("shopify_graphql_query", { query: "mutation { productDelete(input: {id: \"x\"}) { deletedProductId } }" });
    expect(r.isError).toBe(true);
    expect(calls).toHaveLength(0);
  });

  it("strips PII from generic query results", async () => {
    const { call } = await connect(ALL_ENV, [() => ({ data: { customers: { nodes: [{ id: "1", email: "x@y.com", numberOfOrders: "3", defaultAddress: { city: "Madrid" } }] } } })]);
    const r = await call("shopify_graphql_query", { query: "{ customers(first: 1) { nodes { id email numberOfOrders defaultAddress { city } } } }" });
    expect(r.json).toEqual({ customers: { nodes: [{ id: "1", numberOfOrders: "3" }] } });
  });
});

describe("WordPress", () => {
  it("uses application-password basic auth and creates drafts by default", async () => {
    const { call, calls } = await connect({ ...ALL_ENV, MCP_ALLOW_WRITES: "true" }, [
      (c) => (c.url.startsWith("https://blog.example.com/wp-json/wp/v2/posts") && c.method === "POST" ? { id: 42, status: "draft", link: "https://blog.example.com/?p=42" } : undefined),
    ]);
    const r = await call("wordpress_save_content", { title: "Nuevo post", content: "<p>Hola</p>", confirm: true });
    expect(r.json).toEqual({ id: 42, status: "draft", link: "https://blog.example.com/?p=42", created: true });
    expect(calls[0].headers.Authorization).toBe(`Basic ${Buffer.from("editor:abcdefghijklmnop").toString("base64")}`);
    expect(JSON.parse(calls[0].body!)).toEqual({ status: "draft", title: "Nuevo post", content: "<p>Hola</p>" });
  });

  it("strips comment author PII", async () => {
    const { call } = await connect(ALL_ENV, [() => [{ id: 1, post: 2, content: { rendered: "<p>Escríbeme a pepe@mail.com</p>" }, author_email: "pepe@mail.com" }]]);
    const r = await call("wordpress_list_comments");
    expect(r.json.comments[0]).toEqual({ id: 1, post: 2, content: "Escríbeme a [email]" });
  });
});

describe("Cross-platform", () => {
  it("blends platforms, groups by currency and reports partial failures", async () => {
    const { call } = await connect(ALL_ENV, [
      googleToken,
      (c) => (c.url.includes("googleAds:search") ? { results: [{ customer: { currencyCode: "EUR" }, metrics: { costMicros: "100000000", impressions: "10000", clicks: "100", conversions: 10, conversionsValue: 500 } }] } : undefined),
      (c) => (c.url.includes("/act_555/insights") ? { data: [{ account_currency: "EUR", spend: "300", impressions: "30000", clicks: "300", actions: [{ action_type: "purchase", value: "10" }], action_values: [{ action_type: "purchase", value: "900" }] }] } : undefined),
      (c) => (c.url.includes("adAnalytics") ? new Response("li-secret-token expired", { status: 401 }) : undefined),
      (c) => (c.url.includes("report/integrated/get") ? { code: 0, data: { list: [], page_info: { total_page: 1 } } } : undefined),
    ]);
    const r = await call("ads_cross_platform_summary", { date_from: "2026-11-01", date_to: "2026-11-07" });
    const eur = r.json.blended_by_currency.find((b: any) => b.currency === "EUR");
    expect(eur).toMatchObject({ spend: 400, conversions: 20, cpa: 20, roas: 3.5 });
    expect(r.json.platforms.find((p: any) => p.platform === "Meta Ads").share_of_spend_pct).toBe(75);
    expect(r.json.errors).toHaveLength(1);
    expect(r.json.errors[0].platform).toBe("LinkedIn Ads");
    expect(r.text).not.toContain("li-secret-token");
  });
});
