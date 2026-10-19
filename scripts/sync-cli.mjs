#!/usr/bin/env node
// Keep the two served copies of the shell CLI byte-identical to the source of
// truth (packages/cli/bin/usb.sh).
//
// Why this exists: `public/usb` is what https://usbplus.peepsicklabs.com/usb
// serves — the `curl ... | sh` one-liner in every README and in `usb --help`.
// It was a hand-made copy, so the `usb mcp` dependency guard sat in usb.sh for
// four days while the served file still shipped the traceback version: npm
// users had the fix, curl users did not. Copies maintained by hand drift; this
// script (run by `npm run build` via prebuild, so it also runs in CI and on
// every deploy) makes the copy a build artifact instead.
//
// Run directly to sync and exit non-zero if anything changed:
//   node scripts/sync-cli.mjs --check   (exit1 = out of sync, nothing written)
import { copyFileSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, relative } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE = join(root, "packages", "cli", "bin", "usb.sh");
const TARGETS = [join(root, "public", "usb"), join(root, "bin", "usb")];

const source = readFileSync(SOURCE);
const checkOnly = process.argv.includes("--check");
let drifted = 0;

for (const target of TARGETS) {
  let current = null;
  try {
    current = readFileSync(target);
  } catch {
    // Missing target counts as drifted: the served file must exist.
  }
  if (current && current.equals(source)) continue;

  drifted += 1;
  const shown = relative(root, target).replaceAll("\\", "/");
  if (checkOnly) {
    console.error(`out of sync: ${shown} (from packages/cli/bin/usb.sh)`);
  } else {
    copyFileSync(SOURCE, target);
    console.log(`sync-cli: updated ${shown}`);
  }
}

if (checkOnly && drifted > 0) {
  console.error("run: node scripts/sync-cli.mjs   (and commit the result)");
  process.exit(1);
}
