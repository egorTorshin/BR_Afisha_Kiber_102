"use client";
import { ErrorState } from "@/components/ui";
export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <div className="container page-content">
      <ErrorState retry={reset} />
    </div>
  );
}
