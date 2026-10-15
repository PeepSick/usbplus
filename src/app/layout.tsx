import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Footer } from "@/app/components/Footer";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://usbplus.peepsicklabs.com"),
  title: "USB — Universal Systems Bridge | Plug-and-play AI agent capabilities",
  description:
    "One portable capability format for AI agents — installed with sha256-verified installers into 16 runtimes and verified by secscan, a deterministic skill contract verifier. 529 skills from a 65-domain × 8-workflow matrix.",
  applicationName: "USB — Universal Systems Bridge",
  authors: [{ name: "PeepSick Labs" }],
  keywords: [
    "AI skills",
    "agent skills",
    "Claude Code",
    "MCP",
    "LeoSIS",
    "OpenAI Agents",
    "Hermes",
    "Anthropic",
    "OpenRouter",
    "skill bridge",
    "systems bridge",
    "USB",
    "Universal Systems Bridge",
    "secscan",
    "PeepSick Labs",
  ],
  openGraph: {
    title: "USB — Universal Systems Bridge",
    description:
      "One portable capability format for AI agents, installed into 16 runtimes and verified by secscan. 529 skills from a 65-domain × 8-workflow matrix.",
    images: ["/usblg.png"],
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "USB — Universal Systems Bridge",
    description:
      "One portable capability format for AI agents, installed into 16 runtimes and verified by secscan. 529 skills from a 65-domain × 8-workflow matrix.",
    images: ["/usblg.png"],
  },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <script
          src="https://cdn.jsdelivr.net/npm/mermaid@11.16.0/dist/mermaid.min.js"
          integrity="sha384-T/0lMUdJpd2S1ZHtRiofG3htU3xPCrFVeAQ1UUE2TJwlEJSV5NUwn30kP28n238E"
          crossOrigin="anonymous"
          async
        />
        <script
          dangerouslySetInnerHTML={{
            __html: `
              document.addEventListener('DOMContentLoaded', function() {
                if (window.mermaid) {
                  window.mermaid.initialize({ startOnLoad: true, theme: 'dark', themeVariables: { primaryColor: '#0f172a', primaryTextColor: '#fff', primaryBorderColor: '#22d3ee', lineColor: '#22d3ee', secondaryColor: '#1e293b', tertiaryColor: '#0f172a' } });
                }
              });
            `,
          }}
        />
      </head>
      <body className="min-h-screen bg-slate-950 text-slate-100 antialiased">
        <div className="flex min-h-screen flex-col">
          {children}
          <Footer />
        </div>
      </body>
    </html>
  );
}