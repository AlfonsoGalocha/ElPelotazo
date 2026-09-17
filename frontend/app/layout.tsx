import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Football Edge Detector",
  description: "Analisis estadistico y deteccion de edges en mercados de futbol",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es" className="dark">
      <body className="min-h-screen bg-surface font-sans antialiased">
        <header className="border-b border-surface-border bg-surface-raised">
          <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4">
            <Link href="/" className="text-lg font-bold tracking-tight text-slate-100">
              FOOTBALL EDGE DETECTOR
            </Link>
            <nav className="flex gap-5 text-sm text-slate-400">
              <Link href="/" className="hover:text-slate-100">
                Today
              </Link>
              <Link href="/top-signals" className="hover:text-slate-100">
                Top Signals
              </Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
