import type { Metadata } from "next";
import Link from "next/link";
import { CommandPalette } from "@/components/command-palette";
import { Nav } from "@/components/nav";
import { Providers } from "@/components/providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "CareerOS",
  description: "Personal agentic career data platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans">
        <Providers>
          <header className="sticky top-0 z-40 border-b border-line bg-surface/90 backdrop-blur">
            <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3">
              <Link href="/" className="text-base font-bold tracking-tight">
                Career<span className="text-accent">OS</span>
              </Link>
              <Nav />
              <CommandPalette />
            </div>
          </header>
          <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
