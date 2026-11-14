"use client";
import { usePathname } from "next/navigation";
import { Footer, Header } from "@/components/shell";

export function SiteChrome({ position }: { position: "header" | "footer" }) {
  const pathname = usePathname();
  // The admin has its own full-screen shell and navigation. Keep the public
  // site header/footer out of both the login screen and the signed-in panel.
  if (pathname === "/embed" || pathname === "/admin" || pathname.startsWith("/admin/")) return null;
  return position === "header" ? <Header /> : <Footer />;
}
