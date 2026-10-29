import "./globals.css";
import type { Metadata } from "next";
import Search from "@/components/Search";

export const metadata: Metadata = { title: "Drishti", description: "Drishti swing watchlist and charts (paper only)" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="top">
          <span className="brand">Drishti</span>
          <span className="paper">PAPER ONLY</span>
          <a href="/">Watchlist</a>
          <a href="/ops">Ops</a>
          <Search />
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
