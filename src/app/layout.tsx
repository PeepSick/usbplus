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
      <body className="min-h-screen bg-slate-950 text-slate-100 antialiased">
        <div className="flex min-h-screen flex-col">
          {children}
          <Footer />
        </div>
      </body>
    </html>
  );
}
