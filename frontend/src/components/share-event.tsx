"use client";

import { useState } from "react";
import { Check, Share2 } from "lucide-react";

export function ShareEvent() {
  const [copied, setCopied] = useState(false);

  async function share() {
    const url = window.location.href;
    try {
      if (navigator.share) {
        await navigator.share({ title: document.title, url });
        return;
      }
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2500);
    } catch {
      // A dismissed share sheet is not an error the visitor needs to handle.
    }
  }

  return (
    <button className="button button-secondary" type="button" onClick={share}>
      {copied ? <Check size={17} aria-hidden="true" /> : <Share2 size={17} aria-hidden="true" />}
      <span>{copied ? "Ссылка скопирована" : "Поделиться"}</span>
      <span className="sr-only" aria-live="polite">
        {copied ? "Ссылка скопирована в буфер обмена" : ""}
      </span>
    </button>
  );
}
