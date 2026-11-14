import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { loadConfig, type Env } from "../src/config.js";
import { resetGoogleTokenCache } from "../src/connectors/googleAds.js";
import { resetMetaCache } from "../src/connectors/meta.js";
import { buildServer } from "../src/server.js";

export interface Call {
  url: string;
  method: string;
  headers: Record<string, string>;
  body?: string;
}

export type Route = (call: Call) => unknown | undefined;

/** Builds a fake fetch that answers the first matching route and records every call. */
export function fakeFetch(routes: Route[]) {
  const calls: Call[] = [];
  const impl = (async (input: any, init: any = {}) => {
    const call: Call = { url: String(input), method: init.method ?? "GET", headers: init.headers ?? {}, body: init.body };
    calls.push(call);
    for (const route of routes) {
      const out = route(call);
      if (out !== undefined) {
        if (out instanceof Response) return out;
        return new Response(JSON.stringify(out), { status: 200, headers: { "Content-Type": "application/json" } });
      }
    }
    return new Response(JSON.stringify({ error: "no route", url: call.url }), { status: 404 });
  }) as typeof fetch;
  return { impl, calls };
}

export async function connect(env: Env, routes: Route[] = []) {
  resetGoogleTokenCache();
  resetMetaCache();
  const { impl, calls } = fakeFetch(routes);
  const server = buildServer(loadConfig(env), impl, () => new Date("2026-11-14T10:00:00Z"));
  const [clientT, serverT] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: "test", version: "1.0.0" });
  await Promise.all([server.connect(serverT), client.connect(clientT)]);
  const call = async (name: string, args: Record<string, unknown> = {}) => {
    const res: any = await client.callTool({ name, arguments: args });
    const text = res.content?.[0]?.text ?? "";
    return { isError: !!res.isError, text, json: res.isError ? undefined : JSON.parse(text) };
  };
  const tools = async () => (await client.listTools()).tools.map((t) => t.name).sort();
  return { client, call, tools, calls };
}

export const ALL_ENV: Env = {
  GOOGLE_ADS_DEVELOPER_TOKEN: "dev-token-123",
  GOOGLE_ADS_CLIENT_ID: "client-id",
  GOOGLE_ADS_CLIENT_SECRET: "client-secret-xyz",
  GOOGLE_ADS_REFRESH_TOKEN: "refresh-token-xyz",
  GOOGLE_ADS_CUSTOMER_ID: "123-456-7890",
  GOOGLE_ADS_LOGIN_CUSTOMER_ID: "999-000-1111",
  META_ACCESS_TOKEN: "meta-secret-token",
  META_AD_ACCOUNT_ID: "act_555",
  META_PAGE_ID: "777",
  META_INSTAGRAM_ACCOUNT_ID: "888",
  LINKEDIN_ACCESS_TOKEN: "li-secret-token",
  LINKEDIN_AD_ACCOUNT_ID: "5001",
  LINKEDIN_ORGANIZATION_ID: "6001",
  TIKTOK_ADS_ACCESS_TOKEN: "tt-ads-secret",
  TIKTOK_ADS_ADVERTISER_ID: "7001",
  TIKTOK_ACCESS_TOKEN: "tt-secret-token",
  SHOPIFY_SHOP_DOMAIN: "https://demo.myshopify.com/",
  SHOPIFY_ACCESS_TOKEN: "shpat_secret_token",
  WORDPRESS_SITE_URL: "https://blog.example.com/",
  WORDPRESS_USERNAME: "editor",
  WORDPRESS_APPLICATION_PASSWORD: "abcd efgh ijkl mnop",
};
