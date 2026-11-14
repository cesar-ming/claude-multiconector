#!/usr/bin/env node
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { loadConfig } from "./config.js";
import { startHttpServer } from "./httpServer.js";
import { buildServer, connectorStatus } from "./server.js";

async function main(): Promise<void> {
  const config = loadConfig();
  const mode = process.argv.includes("--http") ? "http" : (process.env.MCP_TRANSPORT ?? "stdio").toLowerCase();

  if (mode === "http") {
    await startHttpServer(config);
    return;
  }

  const server = buildServer(config);
  await server.connect(new StdioServerTransport());
  // stdout is reserved for the MCP protocol: log to stderr only, never secrets.
  const enabled = connectorStatus(config).filter((c) => c.configured).map((c) => c.name);
  console.error(`claude-multiconector ready · connectors: ${enabled.join(", ") || "none"} · writes ${config.allowWrites ? "ENABLED" : "disabled"}`);
}

main().catch((err) => {
  console.error("claude-multiconector failed to start:", err instanceof Error ? err.message : err);
  process.exit(1);
});
