/**
 * Remote mode: Streamable HTTP transport protected by OAuth (for claude.ai on
 * web, desktop and the iPhone/Android apps) and/or a static API key (for
 * Claude Code and scripts).
 */
import type { Server } from "node:http";
import express, { type Express } from "express";
import { rateLimit } from "express-rate-limit";
import { requireBearerAuth } from "@modelcontextprotocol/sdk/server/auth/middleware/bearerAuth.js";
import { getOAuthProtectedResourceMetadataUrl, mcpAuthRouter } from "@modelcontextprotocol/sdk/server/auth/router.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { OwnerOAuthProvider } from "./auth.js";
import { type AppConfig, type Env } from "./config.js";
import type { FetchLike } from "./http.js";
import { buildServer, connectorStatus } from "./server.js";

export interface HttpConfig {
  port: number;
  host: string;
  publicUrl?: URL;
  ownerPassword?: string;
  tokenSecret?: string;
  apiKey?: string;
  trustProxy: number;
}

export function loadHttpConfig(env: Env = process.env): HttpConfig {
  const get = (k: string) => env[k]?.trim() || undefined;
  const publicUrl = get("MCP_PUBLIC_URL");
  return {
    port: Number(get("PORT") ?? 8080) || 8080,
    host: get("MCP_HOST") ?? "0.0.0.0",
    publicUrl: publicUrl ? new URL(publicUrl.replace(/\/+$/, "") + "/") : undefined,
    ownerPassword: get("MCP_OWNER_PASSWORD"),
    tokenSecret: get("MCP_TOKEN_SECRET"),
    apiKey: get("MCP_API_KEY"),
    trustProxy: Number(get("MCP_TRUST_PROXY") ?? 1),
  };
}

/** Returns a list of configuration problems that must block startup. */
export function validateHttpConfig(cfg: HttpConfig): string[] {
  const problems: string[] = [];
  const oauthParts = [cfg.publicUrl, cfg.ownerPassword, cfg.tokenSecret].filter(Boolean).length;
  if (oauthParts > 0 && oauthParts < 3) {
    problems.push("OAuth needs MCP_PUBLIC_URL, MCP_OWNER_PASSWORD and MCP_TOKEN_SECRET together.");
  }
  if (oauthParts === 0 && !cfg.apiKey) {
    problems.push("Refusing to start without authentication: set MCP_API_KEY and/or MCP_PUBLIC_URL + MCP_OWNER_PASSWORD + MCP_TOKEN_SECRET.");
  }
  if (cfg.tokenSecret && cfg.tokenSecret.length < 32) problems.push("MCP_TOKEN_SECRET must be at least 32 characters.");
  if (cfg.ownerPassword && cfg.ownerPassword.length < 12) problems.push("MCP_OWNER_PASSWORD must be at least 12 characters.");
  if (cfg.apiKey && cfg.apiKey.length < 32) problems.push("MCP_API_KEY must be at least 32 characters.");
  if (cfg.publicUrl && cfg.publicUrl.protocol !== "https:" && !["localhost", "127.0.0.1"].includes(cfg.publicUrl.hostname)) {
    problems.push("MCP_PUBLIC_URL must use https:// (except localhost).");
  }
  return problems;
}

export function createHttpApp(
  appConfig: AppConfig,
  httpConfig: HttpConfig,
  opts: { fetchImpl?: FetchLike; now?: () => number } = {},
): Express {
  const problems = validateHttpConfig(httpConfig);
  if (problems.length) throw new Error(problems.join(" "));

  const provider = new OwnerOAuthProvider({
    tokenSecret: httpConfig.tokenSecret,
    ownerPassword: httpConfig.ownerPassword,
    apiKey: httpConfig.apiKey,
    now: opts.now,
  });

  const app = express();
  app.disable("x-powered-by");
  app.set("trust proxy", httpConfig.trustProxy);
  app.use((_req, res, next) => {
    res.set({ "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY" });
    next();
  });

  app.get("/healthz", (_req, res) => {
    res.json({ ok: true, oauth: provider.oauthEnabled, api_key: !!httpConfig.apiKey });
  });

  let resourceMetadataUrl: string | undefined;
  const mcpUrl = httpConfig.publicUrl ? new URL("mcp", httpConfig.publicUrl) : undefined;

  if (provider.oauthEnabled && httpConfig.publicUrl && mcpUrl) {
    app.use(
      mcpAuthRouter({
        provider,
        issuerUrl: httpConfig.publicUrl,
        resourceServerUrl: mcpUrl,
        scopesSupported: ["mcp"],
        resourceName: "Claude Multiconector",
        clientRegistrationOptions: { clientIdGeneration: false },
      }),
    );
    resourceMetadataUrl = getOAuthProtectedResourceMetadataUrl(mcpUrl);

    const loginLimiter = rateLimit({ windowMs: 15 * 60 * 1000, limit: 10, standardHeaders: true, legacyHeaders: false });
    app.post("/oauth/login", loginLimiter, express.urlencoded({ extended: false, limit: "16kb" }), (req, res) => {
      res.set("Cache-Control", "no-store");
      const request = typeof req.body?.request === "string" ? req.body.request : "";
      const password = typeof req.body?.password === "string" ? req.body.password : "";
      const result = provider.completeLogin(request, password);
      if (result.ok) {
        res.redirect(302, result.redirect);
        return;
      }
      res.status(result.status).type("html").send(provider.loginErrorPage(request, result.error));
    });
  }

  const auth = requireBearerAuth({ verifier: provider, resourceMetadataUrl });

  // Stateless Streamable HTTP: one MCP server + transport per request.
  app.post("/mcp", auth, express.json({ limit: "1mb" }), async (req, res) => {
    const server = buildServer(appConfig, opts.fetchImpl);
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
    res.on("close", () => {
      void transport.close();
      void server.close();
    });
    try {
      await server.connect(transport);
      await transport.handleRequest(req, res, req.body);
    } catch (err) {
      console.error("MCP request failed:", err instanceof Error ? err.message : "unknown error");
      if (!res.headersSent) {
        res.status(500).json({ jsonrpc: "2.0", error: { code: -32603, message: "Internal server error" }, id: null });
      }
    }
  });

  const methodNotAllowed = (_req: express.Request, res: express.Response) => {
    res.status(405).set("Allow", "POST").json({ jsonrpc: "2.0", error: { code: -32000, message: "Method not allowed." }, id: null });
  };
  app.get("/mcp", auth, methodNotAllowed);
  app.delete("/mcp", auth, methodNotAllowed);

  return app;
}

export async function startHttpServer(appConfig: AppConfig, env: Env = process.env): Promise<Server> {
  const httpConfig = loadHttpConfig(env);
  const app = createHttpApp(appConfig, httpConfig);
  return new Promise((resolve) => {
    const server = app.listen(httpConfig.port, httpConfig.host, () => {
      const enabled = connectorStatus(appConfig).filter((c) => c.configured).map((c) => c.name);
      const authModes = [httpConfig.ownerPassword ? "OAuth" : "", httpConfig.apiKey ? "API key" : ""].filter(Boolean).join(" + ");
      console.error(
        `claude-multiconector HTTP on ${httpConfig.host}:${httpConfig.port}` +
          ` · endpoint ${httpConfig.publicUrl ? new URL("mcp", httpConfig.publicUrl).href : "/mcp"}` +
          ` · auth ${authModes} · connectors: ${enabled.join(", ") || "none"} · writes ${appConfig.allowWrites ? "ENABLED" : "disabled"}`,
      );
      resolve(server);
    });
  });
}
