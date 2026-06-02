import "./globals.scss";
import { AppShell } from "./_components/AppShell";

export const metadata = {
  title: "Lineage Platform",
  description:
    "Multi-parser knowledge graph — Tableau, TWS, QlikView, Spark lineage in one view.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="cds--white">
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
