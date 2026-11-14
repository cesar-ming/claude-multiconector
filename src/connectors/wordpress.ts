import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { stripPii } from "../privacy.js";
import { type Context, registerTool } from "../util.js";

const PLATFORM = "WordPress";

function auth(ctx: Context): Record<string, string> {
  const cfg = ctx.config.wordpress!;
  const token = Buffer.from(`${cfg.username}:${cfg.applicationPassword.replace(/\s+/g, "")}`).toString("base64");
  return { Authorization: `Basic ${token}` };
}

function wp<T = any>(ctx: Context, path: string, opts: { method?: "GET" | "POST"; query?: Record<string, string | number | undefined>; body?: unknown } = {}) {
  return ctx.http.json<T>(PLATFORM, {
    method: opts.method,
    url: `${ctx.config.wordpress!.siteUrl}/wp-json/${path.replace(/^\//, "")}`,
    headers: auth(ctx),
    query: opts.query,
    body: opts.body,
  });
}

function stripHtml(html: string | undefined): string | undefined {
  return html?.replace(/<[^>]*>/g, "").replace(/\s+/g, " ").trim();
}

export function registerWordPress(server: McpServer, ctx: Context): void {
  if (!ctx.config.wordpress) return;

  registerTool(server, ctx, {
    name: "wordpress_site_info",
    title: "WordPress: site info",
    description: "Site title, description, URL, timezone and available REST namespaces (e.g. WooCommerce, Yoast).",
    input: {},
    handler: async () => {
      const root = await wp(ctx, "");
      return {
        name: root.name,
        description: root.description,
        url: root.url,
        timezone: root.timezone_string,
        gmt_offset: root.gmt_offset,
        namespaces: root.namespaces,
      };
    },
  });

  registerTool(server, ctx, {
    name: "wordpress_list_content",
    title: "WordPress: list posts or pages",
    description: "Lists posts or pages with status, dates, link, categories and an excerpt. Supports search and status filters.",
    input: {
      type: z.enum(["posts", "pages"]).optional().describe("Default posts."),
      status: z.enum(["publish", "draft", "pending", "future", "private", "any"]).optional().describe("Default publish."),
      search: z.string().optional(),
      per_page: z.number().int().min(1).max(100).optional().describe("Default 20."),
      page: z.number().int().min(1).optional(),
    },
    handler: async (args) => {
      const items = await wp<any[]>(ctx, `wp/v2/${args.type ?? "posts"}`, {
        query: {
          status: args.status ?? "publish",
          search: args.search,
          per_page: args.per_page ?? 20,
          page: args.page,
          context: "edit",
          _fields: "id,date,modified,status,link,slug,title,excerpt,categories,tags",
        },
      });
      return {
        items: items.map((p) => ({
          id: p.id,
          status: p.status,
          date: p.date,
          modified: p.modified,
          link: p.link,
          slug: p.slug,
          title: p.title?.raw ?? stripHtml(p.title?.rendered),
          excerpt: stripHtml(p.excerpt?.rendered)?.slice(0, 300),
          categories: p.categories,
          tags: p.tags,
        })),
      };
    },
  });

  registerTool(server, ctx, {
    name: "wordpress_get_content",
    title: "WordPress: get a post or page",
    description: "Returns the full content (HTML) and metadata of a post or page by ID.",
    input: { type: z.enum(["posts", "pages"]).optional(), id: z.number().int().positive() },
    handler: async (args) => {
      const p = await wp(ctx, `wp/v2/${args.type ?? "posts"}/${args.id}`, { query: { context: "edit" } });
      return {
        id: p.id,
        status: p.status,
        link: p.link,
        slug: p.slug,
        title: p.title?.raw,
        content: p.content?.raw,
        excerpt: p.excerpt?.raw,
        date: p.date,
        modified: p.modified,
        categories: p.categories,
        tags: p.tags,
      };
    },
  });

  registerTool(server, ctx, {
    name: "wordpress_list_comments",
    title: "WordPress: recent comments",
    description: "Recent comments (content, post, status, date). Author emails and IPs are never returned.",
    input: {
      status: z.enum(["approve", "hold", "spam"]).optional().describe("Default approve."),
      per_page: z.number().int().min(1).max(100).optional(),
    },
    handler: async (args) => {
      const comments = await wp<any[]>(ctx, "wp/v2/comments", {
        query: {
          status: args.status ?? "approve",
          per_page: args.per_page ?? 20,
          _fields: "id,post,date,status,content,link",
        },
      });
      return stripPii({ comments: comments.map((c) => ({ ...c, content: stripHtml(c.content?.rendered) })) });
    },
  });

  registerTool(server, ctx, {
    name: "wordpress_save_content",
    title: "WordPress: create or update a post/page",
    description:
      "Creates a new post/page or updates an existing one (when `id` is given). Defaults to status draft so a human can review before publishing.",
    write: true,
    input: {
      type: z.enum(["posts", "pages"]).optional(),
      id: z.number().int().positive().optional().describe("Existing ID to update; omit to create."),
      title: z.string().optional(),
      content: z.string().optional().describe("HTML or block markup."),
      excerpt: z.string().optional(),
      status: z.enum(["draft", "pending", "publish", "future", "private"]).optional().describe("Default draft."),
      date: z.string().optional().describe("Publish date (site timezone, ISO 8601) for status=future."),
      slug: z.string().optional(),
      categories: z.array(z.number().int()).optional(),
      tags: z.array(z.number().int()).optional(),
    },
    handler: async (args) => {
      const { type, id, ...fields } = args;
      const body = Object.fromEntries(Object.entries({ status: "draft", ...fields }).filter(([, v]) => v !== undefined));
      if (!id && !body.title && !body.content) throw new Error("Provide at least a title or content to create a new item.");
      if (id && args.status === undefined) delete body.status;
      const res = await wp(ctx, `wp/v2/${type ?? "posts"}${id ? `/${id}` : ""}`, { method: "POST", body });
      return { id: res.id, status: res.status, link: res.link, created: !id };
    },
  });
}
