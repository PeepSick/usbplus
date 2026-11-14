import crypto from "node:crypto";
import fs from "node:fs";

// Detached Ed25519 signature over the exact bytes of a generated installer, so
// the CLI can tell "this is what the catalog server produced" from "something
// between the server and me (CDN, cache, proxy, storage) changed it". The
// private key is optional: without it no header is sent and the CLI treats the
// installer as unsigned (self-hosters are unaffected). The matching public key
// is embedded in packages/cli/bin/usb.sh (USB_CATALOG_PUBKEY_DEFAULT).
//
// Configure with USB_CATALOG_SIGNING_KEY_FILE=/path/to/key.pem, or
// USB_CATALOG_SIGNING_KEY with the PEM text ("\n" escapes are accepted).
// This key is separate from the engine-signing key, which never leaves the
// maintainer's machine; this one necessarily lives on the server, so it
// protects the delivery path, not a fully compromised server.

type Loaded = { key: crypto.KeyObject; keyId: string } | null;

let cached: Loaded | undefined;

function load(): Loaded {
  if (cached !== undefined) return cached;
  cached = null;
  try {
    const file = process.env.USB_CATALOG_SIGNING_KEY_FILE;
    const inline = process.env.USB_CATALOG_SIGNING_KEY;
    const pem = file
      ? fs.readFileSync(file, "utf8")
      : inline
        ? inline.replace(/\\n/g, "\n")
        : null;
    if (!pem) return cached;
    const key = crypto.createPrivateKey(pem);
    const der = crypto
      .createPublicKey(key)
      .export({ type: "spki", format: "der" });
    cached = { key, keyId: der.subarray(der.length - 32).toString("hex").slice(0, 16) };
  } catch (error) {
    console.error("catalog signing key could not be loaded; serving unsigned", error);
    cached = null;
  }
  return cached;
}

/** Headers to attach to an installer response; empty when signing is off. */
export function signatureHeaders(body: string): Record<string, string> {
  const loaded = load();
  if (!loaded) return {};
  const sig = crypto.sign(null, Buffer.from(body, "utf8"), loaded.key);
  return {
    "X-USB-Signature": sig.toString("base64"),
    "X-USB-Key-Id": loaded.keyId,
  };
}
