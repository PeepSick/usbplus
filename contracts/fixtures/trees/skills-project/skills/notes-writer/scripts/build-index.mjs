import { writeFile } from "node:fs/promises";

// Local index builder: no network, no secrets.
await writeFile("notes-index.json", JSON.stringify({ ok: true }));
