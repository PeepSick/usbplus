#!/usr/bin/env node
// Publish + sign the CLI's python engines so a curl-installed `usb` can fetch
// them safely (see usb_engines_ensure in packages/cli/bin/usb.sh).
//
//   public/engines/<engine>.py   byte copies of packages/cli/bin/<engine>.py
//   public/engines/engines.json  {schema, version, files:{name: sha256hex}}
//   public/engines/engines.json.sig  base64 Ed25519 signature over those bytes
//
// The client embeds the PUBLIC key (USB_SIGNING_PUBKEY_DEFAULT in usb.sh) and
// refuses any engine whose manifest signature or file hash does not match, so
// a compromised web server or CDN cannot make `usb` run attacker-chosen code.
// The private key never lives in the repo or on the server.
//
//   node scripts/sign-engines.mjs --gen-key [FILE]   create a keypair
//   node scripts/sign-engines.mjs                    republish + sign
//   node scripts/sign-engines.mjs --check            CI: fail if stale/unsigned
//
// Options: --key FILE (default ~/.usb-signing/ed25519.pem, or $USB_SIGNING_KEY_FILE)
//          --out DIR  (default public/engines)   --src DIR (default packages/cli/bin)
//          --pubkey HEX  (check against this key instead of the one in usb.sh)
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const ENGINES = [
  "usb-secscan.py",
  "usb-integrity.py",
  "usb-mcprobe.py",
  "usb-mcp.py",
];

const argv = process.argv.slice(2);
const flag = (n) => argv.includes(n);
const opt = (n, d) => {
  const i = argv.indexOf(n);
  return i >= 0 && argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[i + 1] : d;
};

const srcDir = opt("--src", path.join(root, "packages", "cli", "bin"));
const outDir = opt("--out", path.join(root, "public", "engines"));
const keyFile =
  opt("--key") ||
  process.env.USB_SIGNING_KEY_FILE ||
  path.join(os.homedir(), ".usb-signing", "ed25519.pem");

function rawPublicHex(publicKey) {
  const der = publicKey.export({ type: "spki", format: "der" });
  return der.subarray(der.length - 32).toString("hex");
}

function embeddedPubkey() {
  const sh = fs.readFileSync(
    path.join(root, "packages", "cli", "bin", "usb.sh"),
    "utf8",
  );
  const m = sh.match(/^USB_SIGNING_PUBKEY_DEFAULT="([0-9a-f]{64})"/m);
  if (!m) throw new Error("USB_SIGNING_PUBKEY_DEFAULT not found in usb.sh");
  return m[1];
}

function publicKeyFromHex(hex) {
  const prefix = Buffer.from("302a300506032b6570032100", "hex");
  return crypto.createPublicKey({
    key: Buffer.concat([prefix, Buffer.from(hex, "hex")]),
    format: "der",
    type: "spki",
  });
}

function sha256(buf) {
  return crypto.createHash("sha256").update(buf).digest("hex");
}

function buildManifest() {
  const pkg = JSON.parse(
    fs.readFileSync(path.join(root, "packages", "cli", "package.json"), "utf8"),
  );
  const files = {};
  for (const name of ENGINES) {
    files[name] = sha256(fs.readFileSync(path.join(srcDir, name)));
  }
  return JSON.stringify({ schema: 1, version: pkg.version, files }, null, 2) + "\n";
}

if (flag("--gen-key")) {
  const target = opt("--gen-key", keyFile);
  if (fs.existsSync(target)) {
    console.error(`refusing to overwrite existing key: ${target}`);
    process.exit(2);
  }
  const { privateKey, publicKey } = crypto.generateKeyPairSync("ed25519");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(
    target,
    privateKey.export({ type: "pkcs8", format: "pem" }),
    { mode: 0o600 },
  );
  console.log(`private key written: ${target}  (BACK IT UP; never commit it)`);
  console.log(`public key (hex):    ${rawPublicHex(publicKey)}`);
  process.exit(0);
}

if (flag("--check")) {
  const pubHex = opt("--pubkey", embeddedPubkey());
  const problems = [];
  const mfPath = path.join(outDir, "engines.json");
  let mf;
  try {
    mf = fs.readFileSync(mfPath);
  } catch {
    console.error(`missing ${mfPath} - run: node scripts/sign-engines.mjs`);
    process.exit(1);
  }
  if (mf.toString("utf8") !== buildManifest()) {
    problems.push("engines.json is stale (engine sources or version changed)");
  }
  for (const name of ENGINES) {
    let pub;
    try {
      pub = fs.readFileSync(path.join(outDir, name));
    } catch {
      problems.push(`public copy missing: ${name}`);
      continue;
    }
    if (!pub.equals(fs.readFileSync(path.join(srcDir, name)))) {
      problems.push(`public copy differs from source: ${name}`);
    }
  }
  try {
    const sig = Buffer.from(
      fs.readFileSync(path.join(outDir, "engines.json.sig"), "utf8").trim(),
      "base64",
    );
    if (!crypto.verify(null, mf, publicKeyFromHex(pubHex), sig)) {
      problems.push("engines.json.sig does not verify against the embedded public key");
    }
  } catch {
    problems.push("engines.json.sig missing or unreadable");
  }
  if (problems.length) {
    for (const p of problems) console.error(`engines: ${p}`);
    console.error("fix: node scripts/sign-engines.mjs   (needs the private signing key)");
    process.exit(1);
  }
  console.log("engines: published copies, manifest and signature are current");
  process.exit(0);
}

// publish + sign
if (!fs.existsSync(keyFile)) {
  console.error(`signing key not found: ${keyFile}`);
  console.error("create one with: node scripts/sign-engines.mjs --gen-key");
  process.exit(2);
}
const privateKey = crypto.createPrivateKey(fs.readFileSync(keyFile));
const pubHex = rawPublicHex(crypto.createPublicKey(privateKey));
if (!opt("--out") && pubHex !== embeddedPubkey()) {
  console.error(
    `this key (${pubHex}) is not the public key embedded in usb.sh - clients would reject the signature`,
  );
  process.exit(2);
}
fs.mkdirSync(outDir, { recursive: true });
for (const name of ENGINES) {
  fs.copyFileSync(path.join(srcDir, name), path.join(outDir, name));
}
const manifest = buildManifest();
fs.writeFileSync(path.join(outDir, "engines.json"), manifest);
const sig = crypto.sign(null, Buffer.from(manifest), privateKey);
fs.writeFileSync(path.join(outDir, "engines.json.sig"), sig.toString("base64") + "\n");
console.log(`signed ${ENGINES.length} engines (pubkey ${pubHex.slice(0, 16)}...) -> ${outDir}`);
