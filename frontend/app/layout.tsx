import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import SearchBar from "@/components/SearchBar";

export const metadata: Metadata = {
  title: "Football Edge Detector",
  description: "Analisis estadistico y deteccion de edges en mercados de futbol",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es" className="dark">
      <body className="min-h-screen bg-surface font-sans antialiased">
        <header className="border-b border-surface-border bg-surface-raised">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-4">
            <Link href="/" className="text-lg font-bold tracking-tight text-slate-100">
              FOOTBALL EDGE DETECTOR
            </Link>
            <SearchBar />
            {/* Feedback directo: "demasiadas paginas/secciones" -- se
                mantienen las 5 rutas (nada se borra ni deja de funcionar),
                pero solo las 3 que se usan a diario van en la barra
                principal. Señales/Modelo (vistas tecnicas, para quien
                quiera profundizar) quedan como enlaces mas pequeños y
                discretos al lado, nunca escondidas del todo. */}
            <nav className="flex flex-wrap items-center gap-5 text-sm text-slate-400">
              <Link href="/" className="hover:text-slate-100">
                Inicio
              </Link>
              <Link href="/history" className="hover:text-slate-100">
                Histórico
              </Link>
              <Link href="/jarvis" className="hover:text-slate-100">
                Jarvis
              </Link>
              <span className="mx-1 h-4 w-px bg-surface-border" aria-hidden="true" />
              <Link href="/top-signals" className="text-xs text-slate-500 hover:text-slate-300">
                Señales
              </Link>
              <Link href="/model" className="text-xs text-slate-500 hover:text-slate-300">
                Modelo
              </Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
