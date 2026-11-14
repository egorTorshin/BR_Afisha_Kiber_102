"use client";
import { useEffect } from "react";

export function RecordView({ id }: { id: string }) {
  useEffect(() => {
    const csrf = document.cookie.split("; ").find(x => x.startsWith("user_csrf="))?.split("=")[1];
    if (!csrf) return;
    void fetch(`/backend/api/v1/users/me/history/${encodeURIComponent(id)}`, {
      method: "POST", credentials: "include", headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
    });
  }, [id]);
  return null;
}
