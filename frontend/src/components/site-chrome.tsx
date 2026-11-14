"use client";
import { usePathname } from "next/navigation";
import { Footer, Header } from "@/components/shell";

function isStandalone(pathname: string) {
  return pathname === "/embed" || pathname === "/admin" || pathname.startsWith("/admin/");
}

export function SiteChrome({ position }: { position: "header" | "footer" }) {
  const pathname = usePathname();
  // The admin has its own full-screen shell and navigation. Keep the public
  // site header/footer out of both the login screen and the signed-in panel.
  if (isStandalone(pathname)) return null;
  return position === "header" ? <Header /> : <Footer />;
}

export function SiteMain({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return <main id="main" className={isStandalone(pathname) ? undefined : "public-main"} tabIndex={-1}>{children}</main>;
}
