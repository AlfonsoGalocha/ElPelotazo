import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import Sidebar from "@/components/Sidebar";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });

export const metadata: Metadata = {
  title: "Football Edge Detector",
  description: "Analisis estadistico y deteccion de edges en mercados de futbol",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es" className={`dark ${inter.variable}`}>
      <body className="min-h-screen bg-surface font-sans antialiased">
        <Sidebar />
        {/* `lg:pl-64` deja sitio a la sidebar fija (w-64) solo en
            escritorio; en movil/tablet no hay offset, la sidebar vive en
            el drawer. */}
        <main className="min-h-screen lg:pl-64">
          <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">{children}</div>
        </main>
      </body>
    </html>
  );
}
