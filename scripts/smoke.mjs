// Starts the built server over stdio with dummy credentials and checks it answers.
// No network calls are made: only tools/list and connectors_status.
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const transport = new StdioClientTransport({
  command: process.execPath,
  args: ["dist/index.js"],
  env: { PATH: process.env.PATH ?? "", META_ACCESS_TOKEN: "dummy", SHOPIFY_SHOP_DOMAIN: "demo.myshopify.com", SHOPIFY_ACCESS_TOKEN: "dummy" },
});
const client = new Client({ name: "smoke", version: "1.0.0" });
await client.connect(transport);
const { tools } = await client.listTools();
const status = await client.callTool({ name: "connectors_status", arguments: {} });
await client.close();

const names = tools.map((t) => t.name);
const configured = JSON.parse(status.content[0].text).connectors.filter((c) => c.configured).map((c) => c.name);
if (!names.includes("meta_ads_performance") || !names.includes("shopify_sales_summary") || configured.join() !== "Meta,Shopify") {
  console.error("Smoke test failed", { names, configured });
  process.exit(1);
}
console.log(`Smoke test OK: ${names.length} tools, connectors: ${configured.join(", ")}`);

// ---- HTTP mode: start with an API key and call tools over Streamable HTTP.
import { spawn } from "node:child_process";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const port = 18000 + Math.floor(Math.random() * 1000);
const apiKey = "smoke-".padEnd(40, "x");
const child = spawn(process.execPath, ["dist/index.js", "--http"], {
  env: { PATH: process.env.PATH ?? "", PORT: String(port), MCP_HOST: "127.0.0.1", MCP_API_KEY: apiKey, META_ACCESS_TOKEN: "dummy" },
  stdio: ["ignore", "inherit", "inherit"],
});
try {
  let healthy = false;
  for (let i = 0; i < 50 && !healthy; i++) {
    await new Promise((r) => setTimeout(r, 100));
    healthy = await fetch(`http://127.0.0.1:${port}/healthz`).then((r) => r.ok).catch(() => false);
  }
  if (!healthy) throw new Error("HTTP server did not become healthy");
  const denied = await fetch(`http://127.0.0.1:${port}/mcp`, { method: "POST" });
  if (denied.status !== 401) throw new Error(`expected 401 without token, got ${denied.status}`);
  const http = new Client({ name: "smoke-http", version: "1.0.0" });
  await http.connect(new StreamableHTTPClientTransport(new URL(`http://127.0.0.1:${port}/mcp`), { requestInit: { headers: { Authorization: `Bearer ${apiKey}` } } }));
  const httpTools = (await http.listTools()).tools.length;
  await http.close();
  console.log(`HTTP smoke test OK: ${httpTools} tools`);
} finally {
  child.kill();
}
