import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { AppConfig } from "./config.js";
import { ApiError, HttpClient } from "./http.js";

export interface Context {
  config: AppConfig;
  http: HttpClient;
  /** Injected clock for deterministic tests. */
  now: () => Date;
}

/** Normalised ad metrics used by the cross-platform summary. */
export interface AdMetrics {
  platform: string;
  account: string;
  currency?: string;
  spend: number;
  impressions: number;
  clicks: number;
  conversions: number;
  conversionValue?: number;
}

export interface ToolSpec<S extends z.ZodRawShape> {
  name: string;
  title: string;
  description: string;
  input: S;
  /** Write tools change data on the remote platform and require MCP_ALLOW_WRITES=true. */
  write?: boolean;
  handler: (args: z.infer<z.ZodObject<S>>) => Promise<unknown>;
}

export function registerTool<S extends z.ZodRawShape>(server: McpServer, ctx: Context, spec: ToolSpec<S>): void {
  const description = spec.write
    ? `${spec.description} [WRITE: modifies live data; requires MCP_ALLOW_WRITES=true and confirm=true]`
    : spec.description;
  const input = spec.write ? { ...spec.input, confirm: z.boolean().describe("Must be true to execute this change.") } : spec.input;

  server.registerTool(
    spec.name,
    {
      title: spec.title,
      description,
      inputSchema: input,
      annotations: {
        title: spec.title,
        readOnlyHint: !spec.write,
        destructiveHint: !!spec.write,
        openWorldHint: true,
      },
    },
    (async (args: any) => {
      try {
        if (spec.write) {
          if (!ctx.config.allowWrites) {
            return errorResult(
              `${spec.name} is a write operation and writes are disabled. Set MCP_ALLOW_WRITES=true to enable them.`,
            );
          }
          if (args?.confirm !== true) {
            return errorResult(`${spec.name} requires confirm=true after the user has approved the change.`);
          }
        }
        const { confirm: _confirm, ...rest } = args ?? {};
        const result = await spec.handler(rest);
        return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
      } catch (err) {
        const message = err instanceof ApiError || err instanceof Error ? err.message : String(err);
        return errorResult(ctx.http.redact(message));
      }
    }) as any,
  );
}

export function errorResult(message: string) {
  return { isError: true, content: [{ type: "text" as const, text: message }] };
}

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export const dateRangeShape = {
  date_from: z.string().regex(DATE_RE, "Use YYYY-MM-DD").optional().describe("Start date YYYY-MM-DD (default: 30 days ago)."),
  date_to: z.string().regex(DATE_RE, "Use YYYY-MM-DD").optional().describe("End date YYYY-MM-DD (default: yesterday)."),
};

function iso(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** Resolves an inclusive date range, defaulting to the last 30 complete days. */
export function resolveRange(now: Date, from?: string, to?: string): { from: string; to: string } {
  const yesterday = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - 1));
  const end = to ?? iso(yesterday);
  const start = from ?? iso(new Date(Date.parse(`${end}T00:00:00Z`) - 29 * 86400000));
  if (!DATE_RE.test(start) || !DATE_RE.test(end) || Number.isNaN(Date.parse(start)) || Number.isNaN(Date.parse(end))) {
    throw new Error("Invalid date range; use YYYY-MM-DD.");
  }
  if (start > end) throw new Error(`date_from (${start}) is after date_to (${end}).`);
  return { from: start, to: end };
}

export function requireValue(value: string | undefined, name: string): string {
  if (!value) throw new Error(`Missing ${name}: pass it as an argument or configure its default environment variable.`);
  return value;
}

export function num(value: unknown): number {
  const n = typeof value === "number" ? value : Number(value ?? 0);
  return Number.isFinite(n) ? n : 0;
}

export function round(value: number, decimals = 2): number {
  const f = 10 ** decimals;
  return Math.round(value * f) / f;
}

/** Adds derived KPIs (CTR, CPC, CPA, ROAS) to a metrics record. */
export function withKpis<T extends { spend: number; impressions: number; clicks: number; conversions: number; conversionValue?: number }>(m: T) {
  return {
    ...m,
    spend: round(m.spend),
    ctr: m.impressions ? round((m.clicks / m.impressions) * 100) : null,
    cpc: m.clicks ? round(m.spend / m.clicks) : null,
    cpa: m.conversions ? round(m.spend / m.conversions) : null,
    roas: m.conversionValue && m.spend ? round(m.conversionValue / m.spend) : null,
  };
}
