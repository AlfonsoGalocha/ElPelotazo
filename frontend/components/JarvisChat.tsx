"use client";

import { useEffect, useRef, useState } from "react";
import type { AgentChatMessage, AgentChatResponse } from "@/types";

// Reconocimiento de voz: se probo primero la Web Speech API nativa del
// navegador (gratis, sin dependencias nuevas) pero esa API NO es local
// pese a las apariencias -- manda el audio a un servidor de Google usando
// una clave API que Chrome trae integrada de fabrica. Brave (y cualquier
// Chromium centrado en privacidad) la elimina a proposito, asi que falla
// siempre con "error de red" (bug real confirmado por un usuario,
// reproducido incluso con Shields desactivado). Solucion, la MISMA que usa
// claude.ai: el navegador solo GRABA el audio con `MediaRecorder`
// (funciona en cualquier navegador, incluido Brave/Firefox) y
// `POST /api/agent/transcribe` lo transcribe con un modelo Whisper local
// en nuestro propio backend -- sin API key de pago ni depender de ningun
// servicio externo de Google.
function getSupportedMimeType(): string {
  if (typeof MediaRecorder === "undefined") return "";
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) ?? "";
}

function extensionForMimeType(mimeType: string): string {
  if (mimeType.includes("ogg")) return ".ogg";
  if (mimeType.includes("mp4")) return ".mp4";
  return ".webm";
}

function speak(text: string) {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel(); // corta cualquier respuesta anterior aun hablando
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "es-ES";
  window.speechSynthesis.speak(utterance);
}

export default function JarvisChat() {
  const [messages, setMessages] = useState<AgentChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [voiceReplyEnabled, setVoiceReplyEnabled] = useState(false);
  const [micSupported, setMicSupported] = useState(false);
  const [synthesisSupported, setSynthesisSupported] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  useEffect(() => {
    setMicSupported(
      typeof navigator !== "undefined" &&
        !!navigator.mediaDevices?.getUserMedia &&
        typeof MediaRecorder !== "undefined"
    );
    setSynthesisSupported(typeof window !== "undefined" && "speechSynthesis" in window);
  }, []);

  async function send(overrideText?: string) {
    const text = (overrideText ?? input).trim();
    if (!text || loading) return;

    const history = [...messages, { role: "user" as const, content: text }];
    setMessages(history);
    setInput("");
    setLoading(true);
    setError(null);

    try {
      // Manda el historial ya visto (SIN el ultimo turno, que va en
      // `message`) -- la memoria de conversacion no se guarda en el
      // servidor para este MVP, ver docs/modeling.md.
      const res = await fetch("/api/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history: messages }),
      });
      const data = (await res.json()) as AgentChatResponse | { detail: string };
      if (!res.ok || !("reply" in data)) {
        setError("detail" in data ? data.detail : `Error ${res.status}`);
        return;
      }
      setMessages([...history, { role: "assistant", content: data.reply }]);
      if (voiceReplyEnabled) speak(data.reply);
    } catch {
      setError("No se pudo conectar con Jarvis. Comprueba que el backend esta corriendo.");
    } finally {
      setLoading(false);
    }
  }

  async function transcribeAndSend(blob: Blob, mimeType: string) {
    setTranscribing(true);
    setError(null);
    try {
      const formData = new FormData();
      formData.append("audio", blob, `clip${extensionForMimeType(mimeType)}`);
      const res = await fetch("/api/agent/transcribe", { method: "POST", body: formData });
      const data = (await res.json()) as { text: string } | { detail: string };
      if (!res.ok || !("text" in data)) {
        setError("detail" in data ? data.detail : `Error ${res.status} transcribiendo el audio.`);
        return;
      }
      if (!data.text.trim()) {
        setError("No se detectó ninguna voz en la grabación. Prueba a hablar más alto o más cerca del micrófono.");
        return;
      }
      setInput(data.text);
      send(data.text);
    } catch {
      setError("No se pudo transcribir el audio. Comprueba que el backend está corriendo.");
    } finally {
      setTranscribing(false);
    }
  }

  async function toggleRecording() {
    if (recording) {
      mediaRecorderRef.current?.stop();
      return;
    }

    setError(null);
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      const name = (err as DOMException).name;
      if (name === "NotAllowedError" || name === "PermissionDeniedError") {
        setError(
          "Permiso de micrófono denegado. Revisa el icono de candado/permisos junto a la URL del navegador y permite el micrófono para esta página."
        );
      } else if (name === "NotFoundError") {
        setError("No se encontró ningún micrófono. Comprueba que tienes uno conectado y no lo está usando otra app.");
      } else {
        setError(`No se pudo acceder al micrófono: ${name}`);
      }
      return;
    }

    const mimeType = getSupportedMimeType();
    const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    chunksRef.current = [];
    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data);
    };
    recorder.onstop = () => {
      stream.getTracks().forEach((track) => track.stop()); // apaga el LED del micro
      setRecording(false);
      const blob = new Blob(chunksRef.current, { type: recorder.mimeType });
      transcribeAndSend(blob, recorder.mimeType);
    };
    mediaRecorderRef.current = recorder;
    setRecording(true);
    recorder.start();
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-4 text-xs text-slate-500">
        {synthesisSupported && (
          <label className="flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={voiceReplyEnabled}
              onChange={(e) => setVoiceReplyEnabled(e.target.checked)}
            />
            Leer las respuestas en voz alta
          </label>
        )}
        {!micSupported && (
          <span className="text-amber-500/80">
            Tu navegador no soporta grabación de audio (prueba con Chrome/Edge/Brave/Firefox recientes).
          </span>
        )}
      </div>

      <div className="flex min-h-[300px] flex-col gap-3 rounded-lg border border-surface-border bg-surface-raised p-4">
        {messages.length === 0 && (
          <p className="text-sm text-slate-500">
            Prueba: &quot;¿Qué partidos hay hoy?&quot; o pulsa el micrófono y habla.
          </p>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={
              m.role === "user"
                ? "self-end rounded-lg bg-slate-700 px-3 py-2 text-sm text-slate-100"
                : "self-start rounded-lg bg-black/30 px-3 py-2 text-sm text-slate-200"
            }
          >
            {m.content}
          </div>
        ))}
        {transcribing && <p className="text-xs text-slate-500">Transcribiendo tu voz…</p>}
        {loading && <p className="text-xs text-slate-500">Jarvis está pensando…</p>}
        {error && (
          <p className="rounded border border-red-500/30 bg-red-500/10 p-2 text-xs text-red-300">
            {error}
          </p>
        )}
      </div>
      <div className="flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="Escribe a Jarvis…"
          className="flex-1 rounded border border-surface-border bg-surface-raised px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:border-slate-500 focus:outline-none"
        />
        {micSupported && (
          <button
            onClick={toggleRecording}
            disabled={transcribing}
            title={recording ? "Grabando… pulsa para parar y transcribir" : "Hablar a Jarvis"}
            className={
              "rounded border px-3 py-2 text-sm disabled:opacity-50 " +
              (recording
                ? "animate-pulse border-red-500/50 bg-red-500/15 text-red-300"
                : "border-surface-border text-slate-200 hover:bg-surface-raised")
            }
          >
            🎤
          </button>
        )}
        <button
          onClick={() => send()}
          disabled={loading}
          className="rounded border border-surface-border px-4 py-2 text-sm text-slate-200 hover:bg-surface-raised disabled:opacity-50"
        >
          Enviar
        </button>
      </div>
    </div>
  );
}
