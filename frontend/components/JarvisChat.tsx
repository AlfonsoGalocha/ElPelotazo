"use client";

import { useState } from "react";
import type { AgentChatMessage, AgentChatResponse } from "@/types";

export default function JarvisChat() {
  const [messages, setMessages] = useState<AgentChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send() {
    const text = input.trim();
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
    } catch {
      setError("No se pudo conectar con Jarvis. Comprueba que el backend esta corriendo.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex min-h-[300px] flex-col gap-3 rounded-lg border border-surface-border bg-surface-raised p-4">
        {messages.length === 0 && (
          <p className="text-sm text-slate-500">
            Prueba: &quot;¿Qué partidos hay hoy?&quot;
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
        <button
          onClick={send}
          disabled={loading}
          className="rounded border border-surface-border px-4 py-2 text-sm text-slate-200 hover:bg-surface-raised disabled:opacity-50"
        >
          Enviar
        </button>
      </div>
    </div>
  );
}
