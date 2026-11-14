import type { AddressInfo } from "node:net";
import type { Server } from "node:http";
import { createHash, randomBytes } from "node:crypto";
import { afterEach, describe, expect, it } from "vitest";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { OwnerOAuthProvider } from "../src/auth.js";
import { loadConfig } from "../src/config.js";
import { createHttpApp, loadHttpConfig, validateHttpConfig } from "../src/httpServer.js";

const SECRET = "s".repeat(40);
const PASSWORD = "correct horse battery";
const API_KEY = "k".repeat(40);

let servers: Server[] = [];
afterEach(async () => {
  await Promise.all(servers.map((s) => new Promise((r) => s.close(r))));
  servers = [];
});

async function start(env: Record<string, string>, appEnv: Record<string, string> = { META_ACCESS_TOKEN: "meta" }) {
  // Bind first to learn the port, then build the app with the matching public URL.
  const probe = createHttpApp(loadConfig(appEnv), loadHttpConfig({ MCP_API_KEY: API_KEY }));
  const tmp = probe.listen(0, "127.0.0.1");
  await new Promise((r) => tmp.once("listening", r));
  const port = (tmp.address() as AddressInfo).port;
  await new Promise((r) => tmp.close(r));

  const base = `http://127.0.0.1:${port}`;
  const httpCfg = loadHttpConfig({ ...env, MCP_PUBLIC_URL: env.MCP_PUBLIC_URL === "" ? "" : base, PORT: String(port) });
  const app = createHttpApp(loadConfig(appEnv), httpCfg);
  const server = app.listen(port, "127.0.0.1");
  await new Promise((r) => server.once("listening", r));
  servers.push(server);
  return base;
}

async function mcpClient(base: string, token: string) {
  const client = new Client({ name: "test", version: "1.0.0" });
  await client.connect(
    new StreamableHTTPClientTransport(new URL("/mcp", base), { requestInit: { headers: { Authorization: `Bearer ${token}` } } }),
  );
  return client;
}

const pkce = () => {
  const verifier = randomBytes(32).toString("base64url");
  return { verifier, challenge: createHash("sha256").update(verifier).digest("base64url") };
};

describe("HTTP config validation", () => {
  it("refuses to start without authentication or with weak secrets", () => {
    expect(validateHttpConfig(loadHttpConfig({}))[0]).toMatch(/without authentication/);
    expect(validateHttpConfig(loadHttpConfig({ MCP_API_KEY: "short" }))).toContain("MCP_API_KEY must be at least 32 characters.");
    expect(validateHttpConfig(loadHttpConfig({ MCP_PUBLIC_URL: "https://x.run.app", MCP_OWNER_PASSWORD: PASSWORD }))[0]).toMatch(/together/);
    expect(
      validateHttpConfig(loadHttpConfig({ MCP_PUBLIC_URL: "http://x.example.com", MCP_OWNER_PASSWORD: PASSWORD, MCP_TOKEN_SECRET: SECRET })),
    ).toContain("MCP_PUBLIC_URL must use https:// (except localhost).");
    expect(validateHttpConfig(loadHttpConfig({ MCP_PUBLIC_URL: "https://x.run.app", MCP_OWNER_PASSWORD: PASSWORD, MCP_TOKEN_SECRET: SECRET }))).toEqual([]);
  });
});

describe("HTTP transport with API key", () => {
  it("rejects missing or wrong tokens and serves MCP with the API key", async () => {
    const base = await start({ MCP_API_KEY: API_KEY, MCP_PUBLIC_URL: "" });
    expect((await fetch(`${base}/healthz`)).status).toBe(200);
    expect((await fetch(`${base}/mcp`, { method: "POST" })).status).toBe(401);
    const wrong = await fetch(`${base}/mcp`, { method: "POST", headers: { Authorization: "Bearer nope" } });
    expect(wrong.status).toBe(401);

    const client = await mcpClient(base, API_KEY);
    const tools = (await client.listTools()).tools.map((t) => t.name);
    expect(tools).toContain("meta_ads_performance");
    const status: any = await client.callTool({ name: "connectors_status", arguments: {} });
    expect(JSON.parse(status.content[0].text).connectors.find((c: any) => c.name === "Meta").configured).toBe(true);
    await client.close();
  });
});

describe("HTTP transport with OAuth (claude.ai flow)", () => {
  const env = { MCP_OWNER_PASSWORD: PASSWORD, MCP_TOKEN_SECRET: SECRET };

  it("advertises protected-resource and authorization-server metadata", async () => {
    const base = await start(env);
    const unauth = await fetch(`${base}/mcp`, { method: "POST" });
    expect(unauth.status).toBe(401);
    expect(unauth.headers.get("www-authenticate")).toContain(`resource_metadata="${base}/.well-known/oauth-protected-resource/mcp"`);
    const prm = await (await fetch(`${base}/.well-known/oauth-protected-resource/mcp`)).json();
    expect(prm.resource).toBe(`${base}/mcp`);
    const asm = await (await fetch(`${base}/.well-known/oauth-authorization-server`)).json();
    expect(asm).toMatchObject({ registration_endpoint: `${base}/register`, token_endpoint: `${base}/token`, code_challenge_methods_supported: ["S256"] });
  });

  it("completes registration, login, PKCE token exchange, MCP call and refresh", async () => {
    const base = await start(env);
    const redirect = "https://claude.ai/api/mcp/auth_callback";

    const reg = await fetch(`${base}/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_name: "Claude", redirect_uris: [redirect], token_endpoint_auth_method: "none", grant_types: ["authorization_code", "refresh_token"] }),
    });
    expect(reg.status).toBe(201);
    const client = await reg.json();
    expect(client.client_id).toMatch(/^mc1\./);
    expect(client.client_secret).toBeUndefined();

    const { verifier, challenge } = pkce();
    const authUrl = new URL(`${base}/authorize`);
    Object.entries({ response_type: "code", client_id: client.client_id, redirect_uri: redirect, code_challenge: challenge, code_challenge_method: "S256", state: "xyz", scope: "mcp" }).forEach(([k, v]) =>
      authUrl.searchParams.set(k, v),
    );
    const page = await fetch(authUrl, { redirect: "manual" });
    expect(page.status).toBe(200);
    const html = await page.text();
    expect(html).toContain("Autorizar acceso");
    const request = /name="request" value="([^"]+)"/.exec(html)![1];

    const bad = await fetch(`${base}/oauth/login`, { method: "POST", body: new URLSearchParams({ request, password: "wrong password!" }), redirect: "manual" });
    expect(bad.status).toBe(401);
    expect(await bad.text()).toContain("Contraseña incorrecta");

    const ok = await fetch(`${base}/oauth/login`, { method: "POST", body: new URLSearchParams({ request, password: PASSWORD }), redirect: "manual" });
    expect(ok.status).toBe(302);
    const back = new URL(ok.headers.get("location")!);
    expect(back.origin + back.pathname).toBe(redirect);
    expect(back.searchParams.get("state")).toBe("xyz");
    const code = back.searchParams.get("code")!;

    const tokenReq = (params: Record<string, string>) =>
      fetch(`${base}/token`, { method: "POST", body: new URLSearchParams({ client_id: client.client_id, ...params }) });

    const wrongVerifier = await tokenReq({ grant_type: "authorization_code", code, code_verifier: pkce().verifier, redirect_uri: redirect });
    expect(wrongVerifier.status).toBe(400);

    const tokRes = await tokenReq({ grant_type: "authorization_code", code, code_verifier: verifier, redirect_uri: redirect });
    expect(tokRes.status).toBe(200);
    const tokens = await tokRes.json();
    expect(tokens).toMatchObject({ token_type: "bearer", expires_in: 3600 });

    const replay = await tokenReq({ grant_type: "authorization_code", code, code_verifier: verifier, redirect_uri: redirect });
    expect(replay.status).toBe(400);

    const mcp = await mcpClient(base, tokens.access_token);
    expect((await mcp.listTools()).tools.length).toBeGreaterThan(5);
    await mcp.close();

    const refreshed = await (await tokenReq({ grant_type: "refresh_token", refresh_token: tokens.refresh_token })).json();
    expect(refreshed.access_token).toMatch(/^mc1\./);
    const mcp2 = await mcpClient(base, refreshed.access_token);
    expect((await mcp2.listTools()).tools.length).toBeGreaterThan(5);
    await mcp2.close();

    // A refresh token is not an access token.
    expect((await fetch(`${base}/mcp`, { method: "POST", headers: { Authorization: `Bearer ${tokens.refresh_token}` } })).status).toBe(401);
  });
});

describe("OwnerOAuthProvider tokens", () => {
  it("rejects tampered, expired and pre-password-change tokens", async () => {
    let now = Date.now();
    const p = new OwnerOAuthProvider({ tokenSecret: SECRET, ownerPassword: PASSWORD, now: () => now });
    const token = p.seal("access", 3600, { cid: "c", sc: [] });
    await expect(p.verifyAccessToken(token)).resolves.toMatchObject({ clientId: "c" });

    const tampered = token.slice(0, -4) + (token.endsWith("AAAA") ? "BBBB" : "AAAA");
    await expect(p.verifyAccessToken(tampered)).rejects.toThrow();

    const otherPassword = new OwnerOAuthProvider({ tokenSecret: SECRET, ownerPassword: "a different password", now: () => now });
    await expect(otherPassword.verifyAccessToken(token)).rejects.toThrow();

    const otherSecret = new OwnerOAuthProvider({ tokenSecret: "t".repeat(40), ownerPassword: PASSWORD, now: () => now });
    await expect(otherSecret.verifyAccessToken(token)).rejects.toThrow();

    now += 3601 * 1000;
    await expect(p.verifyAccessToken(token)).rejects.toThrow();
  });
});
