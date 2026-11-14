"use client";

import { Mic, Square } from "lucide-react";
import { useRef, useState } from "react";

export function VoiceInput({ onText, disabled = false, label = "Голосовой ввод" }: {
  onText: (text: string) => void; disabled?: boolean; label?: string;
}) {
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const stopTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  async function start() {
    setError("");
    let stream: MediaStream | null = null;
    try {
      const activeStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream = activeStream;
      const media = new MediaRecorder(activeStream);
      recorder.current = media;
      chunks.current = [];
      media.ondataavailable = event => { if (event.data.size) chunks.current.push(event.data); };
      media.onstop = async () => {
        if (stopTimer.current) clearTimeout(stopTimer.current);
        stopTimer.current = null;
        setRecording(false);
        activeStream.getTracks().forEach(track => track.stop());
        const blob = new Blob(chunks.current, { type: media.mimeType || "audio/webm" });
        if (blob.size > 20 * 1024 * 1024) { setError("Запись больше 20 МБ. Запишите короче."); return; }
        setBusy(true);
        try {
          const extension = blob.type.includes("mp4") ? "m4a" : blob.type.includes("ogg") ? "ogg" : "webm";
          const response = await fetch("/backend/api/v1/speech/transcribe", {
            method: "POST", credentials: "same-origin", headers: { "Content-Type": blob.type, "X-Audio-Filename": `recording.${extension}` }, body: blob,
          });
          const data = await response.json();
          if (!response.ok) throw new Error(data?.error?.message || data?.detail || "Не удалось распознать запись");
          if (!data.text?.trim()) throw new Error("В записи не распознана речь");
          onText(data.text.trim());
        } catch (e) { setError(e instanceof Error ? e.message : "Ошибка распознавания речи"); }
        finally { setBusy(false); }
      };
      media.start();
      setRecording(true);
      stopTimer.current = setTimeout(() => { if (media.state === "recording") media.stop(); }, 20_000);
    } catch { stream?.getTracks().forEach(track => track.stop()); setError("Разрешите доступ к микрофону и попробуйте снова."); }
  }

  function stop() { recorder.current?.stop(); setRecording(false); }
  return <span className="voice-input">
    <button type="button" className="admin-secondary" aria-label={recording ? "Остановить запись" : label} title={recording ? "Остановить запись" : label}
      disabled={disabled || busy} onClick={() => recording ? stop() : void start()}>
      {recording ? <Square size={16}/> : <Mic size={16}/>} {recording ? "Готово" : busy ? "Распознаём…" : "Голос"}
    </button>
    {error && <span className="voice-input-error" role="alert">{error}</span>}
  </span>;
}
