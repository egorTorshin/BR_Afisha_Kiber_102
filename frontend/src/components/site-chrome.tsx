"use client";
import { usePathname } from "next/navigation";
import { Footer, Header } from "@/components/shell";

export function SiteChrome({ position }: { position: "header" | "footer" }) {
  const pathname = usePathname();
  if (pathname === "/embed") return null;
  return position === "header" ? <Header /> : <Footer />;
}
