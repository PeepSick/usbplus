#!/usr/bin/env node
// Render fixture install scripts straight from src/lib/install-script.ts,
// without a running server or database. Used by scripts/check-integrity.sh
// to exercise the installer -> manifest -> verify/lock/diff chain offline.
//
// Usage: node scripts/render-fixture-install.mjs OUTDIR TARGETS SLUGS
//   TARGETS  comma list, e.g. generic,claude
//   SLUGS    comma list of fixture skill slugs (3 => claude small layout,
//            >10 => claude router layout)
// Writes OUTDIR/install-<target>-full.sh and OUTDIR/install-<target>-<slug>.sh
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import ts from "typescript";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const [outdir, targetsArg, slugsArg] = process.argv.slice(2);
if (!outdir || !targetsArg || !slugsArg) {
  console.error("usage: render-fixture-install.mjs OUTDIR TARGETS SLUGS");
  process.exit(2);
}

const src = readFileSync(join(root, "src", "lib", "install-script.ts"), "utf8");
const js = ts.transpileModule(src, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const mod = await import("data:text/javascript," + encodeURIComponent(js));

const skill = (slug) => ({
  slug,
  name: `Fixture ${slug}`,
  category: "Testing",
  description: `Fixture skill ${slug} for offline integrity tests.`,
  triggerPhrase: `Use when testing ${slug}.`,
  promptTemplate: "Follow the fixture protocol.",
  inputs: [{ name: "topic", kind: "string", required: true, description: "Topic" }],
  outputs: [{ name: "result", kind: "string", description: "Result" }],
  examples: ["fixture example"],
  metadata: { risk: "low", modelAgnostic: true, agentAgnostic: true, tags: ["test"] },
});

const slugs = slugsArg.split(",").filter(Boolean);
mkdirSync(outdir, { recursive: true });
for (const target of targetsArg.split(",").filter(Boolean)) {
  const bundle = {
    schemaVersion: "skill-bridge/v1",
    generatedAt: "2026-01-01T00:00:00Z",
    source: "fixture",
    installCommand: "usb install",
    target,
    adapter: "generic",
    pack: {
      slug: "universal-skill-bridge-catalog",
      name: "Fixture Pack",
      description: "offline fixture pack",
      version: "9.9.9",
      author: "test",
    },
    adapters: [],
    skills: slugs.map(skill),
  };
  writeFileSync(join(outdir, `install-${target}-full.sh`),
    mod.renderInstallScript(bundle));
  for (const slug of slugs) {
    writeFileSync(join(outdir, `install-${target}-${slug}.sh`),
      mod.renderInstallScript({ ...bundle, skills: [skill(slug)] }));
  }
}
console.log(`rendered ${slugs.length} skill(s) for ${targetsArg} into ${outdir}`);