/**
 * Stateless OAuth 2.1 authorization server for a single owner.
 *
 * claude.ai custom connectors (web, desktop and mobile apps) authenticate with
 * OAuth + dynamic client registration. This provider implements it without a
 * database, so it survives restarts and scale-to-zero (Cloud Run, Railway…):
 *
 * - every artefact (client registration, login request, authorization code,
 *   access token, refresh token) is a JSON payload encrypted and authenticated
 *   with AES-256-GCM under a key derived from MCP_TOKEN_SECRET;
 * - the owner proves identity with MCP_OWNER_PASSWORD on a login page;
 * - every token embeds a fingerprint of the password, so changing the password
 *   (or MCP_TOKEN_SECRET) revokes every issued token at once;
 * - a static API key (MCP_API_KEY) is also accepted as a bearer token, for
 *   clients that send headers directly (Claude Code, scripts).
 */
import { createCipheriv, createDecipheriv, createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import type { Response } from "express";
import type { OAuthRegisteredClientsStore } from "@modelcontextprotocol/sdk/server/auth/clients.js";
import { InvalidGrantError, InvalidTokenError } from "@modelcontextprotocol/sdk/server/auth/errors.js";
import type { AuthorizationParams, OAuthServerProvider } from "@modelcontextprotocol/sdk/server/auth/provider.js";
import type { AuthInfo } from "@modelcontextprotocol/sdk/server/auth/types.js";
import type { OAuthClientInformationFull, OAuthTokens } from "@modelcontextprotocol/sdk/shared/auth.js";

export const ACCESS_TTL_S = 60 * 60; // 1 hour
export const REFRESH_TTL_S = 30 * 24 * 60 * 60; // 30 days
export const CODE_TTL_S = 5 * 60;
export const LOGIN_TTL_S = 10 * 60;
export const CLIENT_TTL_S = 365 * 24 * 60 * 60;

type Kind = "client" | "login" | "code" | "access" | "refresh";

interface Sealed {
  k: Kind;
  exp: number;
  pw: string;
  [key: string]: unknown;
}

export interface AuthOptions {
  tokenSecret?: string;
  ownerPassword?: string;
  apiKey?: string;
  now?: () => number; // milliseconds
}

function sha256(value: string): Buffer {
  return createHash("sha256").update(value).digest();
}

/** Constant-time string comparison (hashes first so lengths never leak). */
export function safeEqual(a: string, b: string): boolean {
  return timingSafeEqual(sha256(a), sha256(b));
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

export class OwnerOAuthProvider implements OAuthServerProvider {
  private readonly key?: Buffer;
  private readonly pwFingerprint: string;
  private readonly now: () => number;
  /** Best-effort single use for authorization codes within one instance. */
  private readonly usedCodes = new Map<string, number>();

  constructor(private readonly opts: AuthOptions) {
    if (opts.tokenSecret) this.key = createHmac("sha256", opts.tokenSecret).update("claude-multiconector/aes-key/v1").digest();
    this.pwFingerprint = opts.ownerPassword
      ? createHmac("sha256", opts.tokenSecret ?? "").update(`pw:${opts.ownerPassword}`).digest("base64url").slice(0, 16)
      : "";
    this.now = opts.now ?? Date.now;
  }

  /** True when the full OAuth flow (needed by claude.ai) is available. */
  get oauthEnabled(): boolean {
    return !!this.key && !!this.opts.ownerPassword;
  }

  private nowS(): number {
    return Math.floor(this.now() / 1000);
  }

  seal(kind: Kind, ttlS: number, data: Record<string, unknown>): string {
    if (!this.key) throw new Error("MCP_TOKEN_SECRET is not configured");
    const payload: Sealed = { ...data, k: kind, exp: this.nowS() + ttlS, pw: this.pwFingerprint };
    const iv = randomBytes(12);
    const cipher = createCipheriv("aes-256-gcm", this.key, iv);
    const body = Buffer.concat([cipher.update(JSON.stringify(payload), "utf8"), cipher.final()]);
    return `mc1.${Buffer.concat([iv, cipher.getAuthTag(), body]).toString("base64url")}`;
  }

  /** Returns the payload or undefined if the value is forged, expired, of another kind or predates a password change. */
  open<T = Record<string, unknown>>(kind: Kind, value: string | undefined): (T & Sealed) | undefined {
    if (!this.key || !value?.startsWith("mc1.")) return undefined;
    try {
      const raw = Buffer.from(value.slice(4), "base64url");
      if (raw.length < 29) return undefined;
      const decipher = createDecipheriv("aes-256-gcm", this.key, raw.subarray(0, 12));
      decipher.setAuthTag(raw.subarray(12, 28));
      const json = Buffer.concat([decipher.update(raw.subarray(28)), decipher.final()]).toString("utf8");
      const payload = JSON.parse(json) as T & Sealed;
      if (payload.k !== kind || payload.pw !== this.pwFingerprint || payload.exp < this.nowS()) return undefined;
      return payload;
    } catch {
      return undefined;
    }
  }

  // ------------------------------------------------------------------ clients (dynamic registration)
  get clientsStore(): OAuthRegisteredClientsStore {
    return {
      getClient: (clientId: string) => {
        const c = this.open<{ m: OAuthClientInformationFull }>("client", clientId);
        return c ? { ...c.m, client_id: clientId } : undefined;
      },
      registerClient: (client) => {
        const issuedAt = this.nowS();
        const metadata = { ...client, client_id_issued_at: issuedAt } as OAuthClientInformationFull;
        const clientId = this.seal("client", CLIENT_TTL_S, { m: metadata });
        return { ...metadata, client_id: clientId };
      },
    };
  }

  // ------------------------------------------------------------------ authorization (login page)
  async authorize(client: OAuthClientInformationFull, params: AuthorizationParams, res: Response): Promise<void> {
    const request = this.seal("login", LOGIN_TTL_S, {
      cid: client.client_id,
      ru: params.redirectUri,
      cc: params.codeChallenge,
      st: params.state,
      sc: params.scopes ?? [],
      rs: params.resource?.href,
      cn: client.client_name ?? "Claude",
    });
    res.status(200).type("html").send(loginPage(request, client.client_name ?? "Claude", params.redirectUri));
  }

  /**
   * Validates the owner password for a login request and returns the redirect
   * URL carrying the authorization code (or undefined when the request is invalid).
   */
  completeLogin(request: string, password: string): { ok: true; redirect: string } | { ok: false; status: number; error: string } {
    const login = this.open<{ cid: string; ru: string; cc: string; st?: string; sc: string[]; rs?: string }>("login", request);
    if (!login) return { ok: false, status: 400, error: "La solicitud ha caducado. Vuelve a conectar desde Claude." };
    if (!this.opts.ownerPassword || !safeEqual(password, this.opts.ownerPassword)) {
      return { ok: false, status: 401, error: "Contraseña incorrecta." };
    }
    const code = this.seal("code", CODE_TTL_S, { cid: login.cid, ru: login.ru, cc: login.cc, sc: login.sc, rs: login.rs });
    const url = new URL(login.ru);
    url.searchParams.set("code", code);
    if (login.st) url.searchParams.set("state", login.st);
    return { ok: true, redirect: url.href };
  }

  /** Re-renders the login form with an error, keeping the same (still valid) request. */
  loginErrorPage(request: string, error: string): string {
    const login = this.open<{ ru: string; cn?: string }>("login", request);
    return loginPage(request, login?.cn ?? "Claude", login?.ru ?? "", error);
  }

  // ------------------------------------------------------------------ tokens
  private openCode(client: OAuthClientInformationFull, code: string) {
    const c = this.open<{ cid: string; ru: string; cc: string; sc: string[]; rs?: string }>("code", code);
    if (!c || c.cid !== client.client_id) throw new InvalidGrantError("Invalid or expired authorization code");
    return c;
  }

  async challengeForAuthorizationCode(client: OAuthClientInformationFull, authorizationCode: string): Promise<string> {
    return this.openCode(client, authorizationCode).cc;
  }

  private issue(clientId: string, scopes: string[], resource?: string): OAuthTokens {
    return {
      access_token: this.seal("access", ACCESS_TTL_S, { cid: clientId, sc: scopes, rs: resource }),
      token_type: "bearer",
      expires_in: ACCESS_TTL_S,
      refresh_token: this.seal("refresh", REFRESH_TTL_S, { cid: clientId, sc: scopes, rs: resource }),
      scope: scopes.join(" ") || undefined,
    };
  }

  async exchangeAuthorizationCode(
    client: OAuthClientInformationFull,
    authorizationCode: string,
    _codeVerifier?: string,
    redirectUri?: string,
  ): Promise<OAuthTokens> {
    const c = this.openCode(client, authorizationCode);
    if (redirectUri && redirectUri !== c.ru) throw new InvalidGrantError("redirect_uri does not match the authorization request");
    const now = this.now();
    for (const [k, exp] of this.usedCodes) if (exp < now) this.usedCodes.delete(k);
    const fingerprint = sha256(authorizationCode).toString("base64url");
    if (this.usedCodes.has(fingerprint)) throw new InvalidGrantError("Authorization code already used");
    this.usedCodes.set(fingerprint, c.exp * 1000);
    return this.issue(client.client_id, c.sc, c.rs);
  }

  async exchangeRefreshToken(client: OAuthClientInformationFull, refreshToken: string, scopes?: string[]): Promise<OAuthTokens> {
    const r = this.open<{ cid: string; sc: string[]; rs?: string }>("refresh", refreshToken);
    if (!r || r.cid !== client.client_id) throw new InvalidGrantError("Invalid or expired refresh token");
    const granted = scopes?.length ? scopes.filter((s) => r.sc.includes(s)) : r.sc;
    return this.issue(client.client_id, granted, r.rs);
  }

  async verifyAccessToken(token: string): Promise<AuthInfo> {
    if (this.opts.apiKey && safeEqual(token, this.opts.apiKey)) {
      return { token, clientId: "api-key", scopes: ["mcp"], expiresAt: this.nowS() + ACCESS_TTL_S };
    }
    const a = this.open<{ cid: string; sc: string[]; rs?: string }>("access", token);
    if (!a) throw new InvalidTokenError("Invalid or expired access token");
    return { token, clientId: a.cid, scopes: a.sc, expiresAt: a.exp, resource: a.rs ? new URL(a.rs) : undefined };
  }
}

function loginPage(request: string, clientName: string, redirectUri: string, error?: string): string {
  let host = redirectUri;
  try {
    host = new URL(redirectUri).host;
  } catch {
    /* keep raw */
  }
  return `<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Multiconector · Autorizar</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--fg:#1d1b18;--muted:#6b665e;--accent:#c4613b;--err:#b3261e;--line:#e3dfd8}
@media (prefers-color-scheme:dark){:root{--bg:#1b1a18;--card:#262421;--fg:#f1eee8;--muted:#a9a399;--line:#3a3733}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--fg);font:16px/1.5 -apple-system,system-ui,Segoe UI,Roboto,sans-serif;padding:16px}
main{width:100%;max-width:380px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:28px}
h1{font-size:20px;margin:0 0 6px}p{margin:0 0 18px;color:var(--muted);font-size:14px}
label{display:block;font-size:14px;margin-bottom:6px}input{width:100%;font:inherit;padding:12px;border-radius:10px;border:1px solid var(--line);background:var(--bg);color:var(--fg)}
button{margin-top:16px;width:100%;font:inherit;font-weight:600;padding:12px;border:0;border-radius:10px;background:var(--accent);color:#fff;cursor:pointer}
.err{color:var(--err);font-size:14px;margin:0 0 12px}
</style></head><body><main>
<h1>Autorizar acceso</h1>
<p><strong>${escapeHtml(clientName)}</strong> quiere usar tu multiconector (Google Ads, Meta, LinkedIn, TikTok, Shopify, WordPress). Volverás a <strong>${escapeHtml(host)}</strong>.</p>
${error ? `<p class="err" role="alert">${escapeHtml(error)}</p>` : ""}
<form method="post" action="/oauth/login">
<input type="hidden" name="request" value="${escapeHtml(request)}">
<label for="pw">Contraseña del propietario</label>
<input id="pw" name="password" type="password" autocomplete="current-password" required autofocus>
<button type="submit">Autorizar</button>
</form></main></body></html>`;
}

