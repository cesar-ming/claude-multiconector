/**
 * Thin JSON-over-HTTPS client shared by every connector.
 * - enforces a timeout,
 * - never echoes credentials back in errors (Authorization headers, tokens),
 * - truncates large error bodies.
 */

export type FetchLike = typeof fetch;

export class ApiError extends Error {
  constructor(
    public readonly platform: string,
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface HttpRequest {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  url: string;
  headers?: Record<string, string>;
  query?: Record<string, string | number | boolean | undefined>;
  body?: unknown;
  form?: Record<string, string>;
}

export class HttpClient {
  constructor(
    private readonly fetchImpl: FetchLike,
    private readonly timeoutMs: number,
    private readonly secrets: string[] = [],
  ) {}

  redact(text: string): string {
    let out = text;
    for (const secret of this.secrets) {
      out = out.split(secret).join("[REDACTED]");
    }
    // Generic patterns: bearer tokens and access_token query params.
    out = out.replace(/(Bearer\s+)[A-Za-z0-9._~+/=-]+/gi, "$1[REDACTED]");
    out = out.replace(/(access_token=)[^&\s"]+/gi, "$1[REDACTED]");
    return out;
  }

  async json<T = any>(platform: string, req: HttpRequest): Promise<T> {
    const url = new URL(req.url);
    for (const [key, value] of Object.entries(req.query ?? {})) {
      if (value !== undefined) url.searchParams.set(key, String(value));
    }

    const headers: Record<string, string> = { Accept: "application/json", ...req.headers };
    let body: string | undefined;
    if (req.form) {
      headers["Content-Type"] = "application/x-www-form-urlencoded";
      body = new URLSearchParams(req.form).toString();
    } else if (req.body !== undefined) {
      headers["Content-Type"] = headers["Content-Type"] ?? "application/json";
      body = JSON.stringify(req.body);
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    let res: Response;
    try {
      res = await this.fetchImpl(url.toString(), {
        method: req.method ?? (body ? "POST" : "GET"),
        headers,
        body,
        signal: controller.signal,
      });
    } catch (err) {
      const reason = err instanceof Error && err.name === "AbortError" ? `timeout after ${this.timeoutMs} ms` : String(err);
      throw new ApiError(platform, 0, this.redact(`${platform}: network error (${reason})`));
    } finally {
      clearTimeout(timer);
    }

    const text = await res.text();
    if (!res.ok) {
      const snippet = text.length > 1500 ? `${text.slice(0, 1500)}…` : text;
      throw new ApiError(platform, res.status, this.redact(`${platform}: HTTP ${res.status} ${snippet}`));
    }
    if (!text) return {} as T;
    try {
      return JSON.parse(text) as T;
    } catch {
      throw new ApiError(platform, res.status, `${platform}: invalid JSON response`);
    }
  }
}
