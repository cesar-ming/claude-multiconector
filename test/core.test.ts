import { describe, expect, it } from "vitest";
import { loadConfig } from "../src/config.js";
import { isMutation } from "../src/connectors/shopify.js";
import { HttpClient } from "../src/http.js";
import { stripPii } from "../src/privacy.js";
import { resolveRange, withKpis } from "../src/util.js";
import { ALL_ENV, connect } from "./helpers.js";

describe("config", () => {
  it("enables only connectors with complete credentials", () => {
    const cfg = loadConfig({ META_ACCESS_TOKEN: "x", GOOGLE_ADS_DEVELOPER_TOKEN: "only-one" });
    expect(cfg.meta).toBeDefined();
    expect(cfg.googleAds).toBeUndefined();
    expect(cfg.allowWrites).toBe(false);
  });

  it("normalises ids and urls", () => {
    const cfg = loadConfig(ALL_ENV);
    expect(cfg.googleAds?.defaultCustomerId).toBe("1234567890");
    expect(cfg.shopify?.shopDomain).toBe("demo.myshopify.com");
    expect(cfg.wordpress?.siteUrl).toBe("https://blog.example.com");
  });
});

describe("utils", () => {
  it("defaults to the last 30 complete days", () => {
    expect(resolveRange(new Date("2026-11-14T10:00:00Z"))).toEqual({ from: "2026-10-15", to: "2026-11-13" });
    expect(() => resolveRange(new Date(), "2026-02-01", "2026-01-01")).toThrow(/after/);
  });

  it("computes KPIs safely", () => {
    const k = withKpis({ spend: 100, impressions: 10000, clicks: 200, conversions: 4, conversionValue: 400 });
    expect(k).toMatchObject({ ctr: 2, cpc: 0.5, cpa: 25, roas: 4 });
    expect(withKpis({ spend: 0, impressions: 0, clicks: 0, conversions: 0 })).toMatchObject({ ctr: null, cpa: null, roas: null });
  });

  it("redacts secrets and bearer tokens", () => {
    const http = new HttpClient(fetch, 1000, ["super-secret-value"]);
    expect(http.redact("x super-secret-value Bearer abc.def access_token=zzz&a=1")).toBe(
      "x [REDACTED] Bearer [REDACTED] access_token=[REDACTED]&a=1",
    );
  });
});

describe("privacy", () => {
  it("removes personal data keys and masks emails", () => {
    const out = stripPii({
      orders: [{ id: 1, email: "a@b.com", customer: { firstName: "Ana" }, shippingAddress: { address1: "x" }, note: "call me", total: "10.00", tags: "vip contact ana@x.es" }],
      comments: [{ author_email: "c@d.com", author_ip: "1.2.3.4", content: "hola" }],
    });
    expect(out).toEqual({ orders: [{ id: 1, total: "10.00", tags: "vip contact [email]" }], comments: [{ content: "hola" }] });
  });

  it("detects GraphQL mutations but ignores strings and comments", () => {
    expect(isMutation("mutation { productDelete(input:{id:\"1\"}) { deletedProductId } }")).toBe(true);
    expect(isMutation("  { shop { name } }")).toBe(false);
    expect(isMutation('query { products(query: "mutation") { nodes { id } } }')).toBe(false);
    expect(isMutation("# mutation\n{ shop { name } }")).toBe(false);
  });
});

describe("server", () => {
  it("only exposes connectors_status without credentials", async () => {
    const { tools, call } = await connect({});
    expect(await tools()).toEqual(["connectors_status"]);
    const status = await call("connectors_status");
    expect(status.json.connectors.every((c: any) => c.configured === false)).toBe(true);
  });

  it("exposes every connector when fully configured", async () => {
    const { tools } = await connect(ALL_ENV);
    const names = await tools();
    for (const prefix of ["google_ads_", "meta_ads_", "facebook_", "instagram_", "linkedin_ads_", "linkedin_organization", "tiktok_ads_", "tiktok_profile", "shopify_", "wordpress_", "ads_cross_platform_summary"]) {
      expect(names.some((n) => n.startsWith(prefix))).toBe(true);
    }
    expect(names).not.toContain("tiktok_ads_list_advertisers");
  });

  it("blocks write tools unless writes are enabled and confirmed", async () => {
    const off = await connect(ALL_ENV);
    const r1 = await off.call("facebook_publish_post", { message: "hola", confirm: true });
    expect(r1.isError).toBe(true);
    expect(r1.text).toMatch(/MCP_ALLOW_WRITES/);
    expect(off.calls).toHaveLength(0);

    const on = await connect({ ...ALL_ENV, MCP_ALLOW_WRITES: "true" });
    const r2 = await on.call("facebook_publish_post", { message: "hola", confirm: false });
    expect(r2.isError).toBe(true);
    expect(r2.text).toMatch(/confirm=true/);
    expect(on.calls).toHaveLength(0);
  });

  it("never leaks secrets in error messages", async () => {
    const { call } = await connect(ALL_ENV, [
      (c) => (c.url.includes("graph.facebook.com") ? new Response(`bad token meta-secret-token`, { status: 401 }) : undefined),
    ]);
    const r = await call("meta_ads_list_accounts");
    expect(r.isError).toBe(true);
    expect(r.text).toContain("HTTP 401");
    expect(r.text).not.toContain("meta-secret-token");
  });
});
