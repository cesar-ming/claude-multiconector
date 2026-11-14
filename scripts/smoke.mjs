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
