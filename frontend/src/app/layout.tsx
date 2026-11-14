import type { Metadata } from "next";
import { headers } from "next/headers";
import { SiteChrome, SiteMain } from "@/components/site-chrome";
import "./globals.css";
import "./afisha-theme.css";
export const metadata: Metadata = {
  title: {
    default: "Культура Башкортостана — рядом с вами",
    template: "%s · Культура Башкортостана",
  },
  description:
    "Афиша культурных событий Башкортостана: спектакли, концерты, выставки и новые впечатления рядом с вами.",
};
export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  await headers();
  return (
    <html lang="ru">
      <body>
        <a className="skip-link" href="#main">
          Перейти к содержимому
        </a>
        <SiteChrome position="header" />
        <SiteMain>{children}</SiteMain>
        <SiteChrome position="footer" />
      </body>
    </html>
  );
}
