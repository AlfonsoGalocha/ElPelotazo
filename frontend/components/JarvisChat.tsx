"use client";

import { useEffect, useRef, useState } from "react";
import type { AgentChatMessage, AgentChatResponse } from "@/types";

// Web Speech API: nativa del navegador, sin coste ni claves nuevas (Fase
// 7 del brief -- "primero estudia las opciones", elegido esto por ser
// gratis; ElevenLabs/Whisper quedan como mejora futura si la calidad de
// voz robotica del sistema operativo no basta). Soporte real: Chrome/Edge
// completo, Safari parcial, Firefox SIN reconocimiento de voz -- se
// detecta y se deshabilita el microfono en vez de fingir que funciona.
type SpeechRecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: any) => void) | null; // eslint-disable-line @typescript-eslint/no-explicit-any
  onerror: ((event: any) => void) | null; // eslint-disable-line @typescript-eslint/no-explicit-any
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
};

function getSpeechRecognition(): (new () => SpeechRecognitionLike) | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: new () => SpeechRecognitionLike;
    webkitSpeechRecognition?: new () => SpeechRecognitionLike;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
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
  const [error, setError] = useState<string | null>(null);
  const [listening, setListening] = useState(false);
  const [voiceReplyEnabled, setVoiceReplyEnabled] = useState(false);
  const [speechSupported, setSpeechSupported] = useState(false);
  const [synthesisSupported, setSynthesisSupported] = useState(false);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);

  useEffect(() => {
    setSpeechSupported(getSpeechRecognition() !== null);
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

  function toggleListening() {
    const SpeechRecognitionCtor = getSpeechRecognition();
    if (!SpeechRecognitionCtor) return;

    if (listening) {
      recognitionRef.current?.stop();
      return;
    }

    const recognition = new SpeechRecognitionCtor();
    recognition.lang = "es-ES";
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.onresult = (event) => {
      const transcript = event.results[0][0].transcript as string;
      setInput(transcript);
      send(transcript);
    };
    recognition.onerror = () => setListening(false);
    recognition.onend = () => setListening(false);
    recognitionRef.current = recognition;
    setListening(true);
    recognition.start();
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
        {!speechSupported && (
          <span className="text-amber-500/80">
            Tu navegador no soporta reconocimiento de voz (prueba con Chrome/Edge).
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
        {speechSupported && (
          <button
            onClick={toggleListening}
            title={listening ? "Escuchando… pulsa para parar" : "Hablar a Jarvis"}
            className={
              "rounded border px-3 py-2 text-sm " +
              (listening
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
