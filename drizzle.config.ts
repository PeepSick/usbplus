import { defineConfig } from "drizzle-kit";

// DATABASE_URL comes from the environment so one config can push the schema
// into whatever database a deployment points at: docker compose, production,
// or the release harness. The literal fallback keeps the documented
// local-dev database working when no URL is exported.
//
// (A plain drizzle.config.json cannot read the environment, and passing
// --url on the CLI makes drizzle-kit skip config loading entirely — it then
// fails with "dialect: undefined, schema: undefined".)
const url =
  process.env.DATABASE_URL ??
  "postgresql://postgres:postgres@127.0.0.1:5432/app_db";

export default defineConfig({
  dialect: "postgresql",
  schema: "./src/db/schema.ts",
  dbCredentials: { url },
});
