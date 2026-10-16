import { DEFAULT_PACK } from "@/lib/default-skillpack";
import { Github, Terminal } from "lucide-react";

const INSTALL_COMMANDS = [
  "npm install -g @peepsick/usb-cli",
  "curl -fsSL https://usbplus.peepsicklabs.com/usb | bash",
] as const;

/**
 * Minimal landing for usbplus v1: product name, version, the two install
 * paths, and the links a first-time visitor actually needs. The catalog and
 * installer live behind the /api/* routes; this page deliberately does not
 * query the database so it prerenders statically.
 */
export default function HomePage() {
  return (
    <main className="relative flex-1 overflow-hidden">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-[420px] bg-[radial-gradient(ellipse_at_top,rgba(34,211,238,0.14),transparent_65%)]"
      />

      <section className="relative mx-auto max-w-4xl px-6 pb-24 pt-24 text-center sm:pt-32">
        <span className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-4 py-1.5 font-mono text-xs tracking-wide text-cyan-300">
          open source · MIT · v{DEFAULT_PACK.version}
        </span>

        <h1 className="mt-8 text-5xl font-black tracking-tight sm:text-6xl">
          <span className="bg-gradient-to-r from-cyan-300 via-sky-300 to-violet-400 bg-clip-text text-transparent">
            usb
          </span>
          <span className="mt-2 block text-slate-100">Universal Systems Bridge</span>
        </h1>

        <p className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-slate-400">
          One portable capability format for AI agents — sha256-verified installers into 16
          runtimes, every skill checked offline by secscan, a deterministic contract verifier.
        </p>

        <div className="mx-auto mt-10 max-w-xl space-y-3 text-left">
          {INSTALL_COMMANDS.map((cmd) => (
            <div
              key={cmd}
              className="flex items-center gap-3 rounded-lg border border-white/10 bg-slate-950/80 px-4 py-3 shadow-[0_0_30px_rgba(0,0,0,0.4)]"
            >
              <Terminal className="h-4 w-4 flex-shrink-0 text-cyan-300" />
              <code className="break-all font-mono text-xs text-slate-300 sm:text-sm">
                {cmd}
              </code>
            </div>
          ))}
        </div>

        <div className="mt-10 flex flex-wrap items-center justify-center gap-4 text-sm">
          <a
            href="https://github.com/PeepSick/usbplus"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-2 rounded-lg border border-white/10 bg-white/5 px-4 py-2 text-slate-200 transition hover:border-cyan-400/40 hover:text-cyan-300"
          >
            <Github className="h-4 w-4" />
            GitHub
          </a>
          <a
            href="https://www.npmjs.com/package/@peepsick/usb-cli"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-2 rounded-lg border border-white/10 bg-white/5 px-4 py-2 text-slate-200 transition hover:border-cyan-400/40 hover:text-cyan-300"
          >
            npm package
          </a>
          <a
            href="/api/version"
            className="inline-flex items-center gap-2 rounded-lg border border-white/10 bg-white/5 px-4 py-2 text-slate-200 transition hover:border-cyan-400/40 hover:text-cyan-300"
          >
            version API (JSON)
          </a>
        </div>

        <dl className="mx-auto mt-16 grid max-w-2xl grid-cols-3 gap-4 border-t border-white/10 pt-8 text-center">
          <div>
            <dt className="font-mono text-2xl font-bold text-cyan-300">529</dt>
            <dd className="mt-1 text-xs uppercase tracking-widest text-slate-500">
              verified skills
            </dd>
          </div>
          <div>
            <dt className="font-mono text-2xl font-bold text-cyan-300">16</dt>
            <dd className="mt-1 text-xs uppercase tracking-widest text-slate-500">
              runtime targets
            </dd>
          </div>
          <div>
            <dt className="font-mono text-2xl font-bold text-cyan-300">100%</dt>
            <dd className="mt-1 text-xs uppercase tracking-widest text-slate-500">
              offline engine
            </dd>
          </div>
        </dl>
      </section>
    </main>
  );
}
