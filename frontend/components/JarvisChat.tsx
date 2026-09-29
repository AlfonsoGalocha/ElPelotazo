"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { AgentChatMessage, AgentChatResponse, AgentMatchReference } from "@/types";

type ChatMessage = AgentChatMessage & { matches?: AgentMatchReference[] };

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

// Detecta silencio tras haber detectado voz, para poder parar de grabar
// SOLA sin que el usuario tenga que pulsar nada (modo "conversacion
// continua" pedido por un usuario: "no tener que darle al boton de hablar
// todo el rato"). Analisis de volumen simple (RMS sobre la forma de onda),
// nada de reconocimiento de voz -- eso ya lo hace Whisper en el backend.
function monitorSilence(stream: MediaStream, onSilence: () => void): () => void {
  const AudioCtx = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  const audioCtx = new AudioCtx();
  const source = audioCtx.createMediaStreamSource(stream);
  const analyser = audioCtx.createAnalyser();
  analyser.fftSize = 2048;
  source.connect(analyser);
  const data = new Uint8Array(analyser.fftSize);

  const SILENCE_THRESHOLD = 0.02;
  const SILENCE_DURATION_MS = 1200;
  const MAX_DURATION_MS = 15000;
  const startedAt = Date.now();
  let speechDetected = false;
  let silenceStart: number | null = null;
  let rafId = 0;
  let cleaned = false;

  function tick() {
    analyser.getByteTimeDomainData(data);
    let sumSquares = 0;
    for (let i = 0; i < data.length; i++) {
      const v = (data[i] - 128) / 128;
      sumSquares += v * v;
    }
    const rms = Math.sqrt(sumSquares / data.length);

    if (rms > SILENCE_THRESHOLD) {
      speechDetected = true;
      silenceStart = null;
    } else if (speechDetected && silenceStart === null) {
      silenceStart = Date.now();
    }

    const silentLongEnough = silenceStart !== null && Date.now() - silenceStart > SILENCE_DURATION_MS;
    const tooLong = Date.now() - startedAt > MAX_DURATION_MS;
    if (silentLongEnough || tooLong) {
      cleanup();
      onSilence();
      return;
    }
    rafId = requestAnimationFrame(tick);
  }

  function cleanup() {
    if (cleaned) return;
    cleaned = true;
    cancelAnimationFrame(rafId);
    audioCtx.close().catch(() => {});
  }

  rafId = requestAnimationFrame(tick);
  return cleanup;
}

// Lectura de respuestas en voz alta: se probo primero `speechSynthesis`
// nativa del navegador (gratis) pero en Linux (Brave/Chromium) reporta 0
// voces instaladas -- depende de voces remotas de Google no disponibles
// ahi (bug real confirmado por un usuario, misma familia de problema que
// el microfono). Ahora el audio se genera en el backend con Piper
// (POST /api/agent/speak) y se reproduce con el elemento <audio>, que no
// depende de ninguna voz del sistema.
//
// `audio.play()` devuelve una promesa que los navegadores pueden RECHAZAR
// en silencio por su politica de autoplay (p.ej. si consideran que ya paso
// demasiado tiempo desde el ultimo gesto del usuario -- en modo
// conversacion continua, solo el PRIMER turno tiene un click real detras,
// asi que es esperable que los siguientes necesiten el boton manual). Sin
// capturar ese rechazo, "no suena" y no hay ningun error visible (bug real
// reportado por un usuario). `speak` devuelve una promesa que se resuelve
// cuando el audio termina de sonar (o de inmediato si el autoplay fallo),
// para que el modo continuo sepa cuando es seguro volver a escuchar sin
// captarse a si mismo por el microfono.
let currentAudio: HTMLAudioElement | null = null;

async function speak(
  text: string,
  onError: (message: string) => void,
  onAutoplayBlocked: (audio: HTMLAudioElement) => void
): Promise<void> {
  currentAudio?.pause(); // corta cualquier respuesta anterior aun sonando
  try {
    const res = await fetch("/api/agent/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) {
      const data = (await res.json()) as { detail?: string };
      onError(data.detail ?? `Error ${res.status} generando el audio.`);
      return;
    }
    const blob = await res.blob();
    const audio = new Audio(URL.createObjectURL(blob));
    currentAudio = audio;
    await new Promise<void>((resolve) => {
      audio.onended = () => resolve();
      audio.play().catch(() => {
        // Autoplay bloqueado por el navegador -- no es un fallo real (el
        // audio SI se genero bien), solo hace falta pulsar play a mano. No
        // bloqueamos el modo continuo esperando ese click.
        onAutoplayBlocked(audio);
        resolve();
      });
    });
  } catch {
    onError("No se pudo generar el audio de la respuesta. Comprueba que el backend está corriendo.");
  }
}

export default function JarvisChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [continuousMode, setContinuousMode] = useState(false);
  const [voiceReplyEnabled, setVoiceReplyEnabled] = useState(false);
  const [micSupported, setMicSupported] = useState(false);
  const [blockedAudio, setBlockedAudio] = useState<HTMLAudioElement | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  // Refs (no state) porque las lee codigo async/recursivo (el bucle de
  // conversacion continua) que no debe quedarse con un valor de closure
  // desactualizado.
  const continuousModeRef = useRef(false);
  const voiceReplyEnabledRef = useRef(false);

  useEffect(() => {
    setMicSupported(
      typeof navigator !== "undefined" &&
        !!navigator.mediaDevices?.getUserMedia &&
        typeof MediaRecorder !== "undefined"
    );
  }, []);

  useEffect(() => {
    voiceReplyEnabledRef.current = voiceReplyEnabled;
  }, [voiceReplyEnabled]);

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
      // servidor para este MVP, ver docs/modeling.md. Solo role/content:
      // el historial local puede llevar campos extra (`matches`) que el
      // backend no espera.
      const res = await fetch("/api/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          history: messages.map(({ role, content }) => ({ role, content })),
        }),
      });
      const data = (await res.json()) as AgentChatResponse | { detail: string };
      if (!res.ok || !("reply" in data)) {
        setError("detail" in data ? data.detail : `Error ${res.status}`);
        return;
      }
      setMessages([
        ...history,
        { role: "assistant", content: data.reply, matches: data.referenced_matches },
      ]);
      if (voiceReplyEnabledRef.current) {
        setBlockedAudio(null);
        await speak(data.reply, setError, setBlockedAudio);
      }
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
        // En modo continuo esto pasa a menudo (silencio/ruido de fondo) --
        // no es un error real, el bucle simplemente vuelve a escuchar.
        if (!continuousModeRef.current) {
          setError("No se detectó ninguna voz en la grabación. Prueba a hablar más alto o más cerca del micrófono.");
        }
        return;
      }
      await send(data.text);
    } catch {
      setError("No se pudo transcribir el audio. Comprueba que el backend está corriendo.");
    } finally {
      setTranscribing(false);
    }
  }

  function startListening(autoStopOnSilence: boolean) {
    setError(null);
    navigator.mediaDevices
      .getUserMedia({ audio: true })
      .then((stream) => {
        const mimeType = getSupportedMimeType();
        const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
        chunksRef.current = [];
        recorder.ondataavailable = (e) => {
          if (e.data.size > 0) chunksRef.current.push(e.data);
        };

        const stopSilenceMonitor = autoStopOnSilence
          ? monitorSilence(stream, () => {
              if (recorder.state === "recording") recorder.stop();
            })
          : null;

        recorder.onstop = async () => {
          stopSilenceMonitor?.();
          stream.getTracks().forEach((track) => track.stop()); // apaga el LED del micro
          setRecording(false);
          const blob = new Blob(chunksRef.current, { type: recorder.mimeType });
          await transcribeAndSend(blob, recorder.mimeType);
          // Conversacion continua: en cuanto se envia y se lee la
          // respuesta (si toca), se vuelve a escuchar sola -- sin pulsar
          // nada. Se corta si el usuario desactivo el modo mientras tanto.
          if (continuousModeRef.current) {
            startListening(true);
          }
        };
        mediaRecorderRef.current = recorder;
        setRecording(true);
        recorder.start();
      })
      .catch((err: DOMException) => {
        if (err.name === "NotAllowedError" || err.name === "PermissionDeniedError") {
          setError(
            "Permiso de micrófono denegado. Revisa el icono de candado/permisos junto a la URL del navegador y permite el micrófono para esta página."
          );
        } else if (err.name === "NotFoundError") {
          setError("No se encontró ningún micrófono. Comprueba que tienes uno conectado y no lo está usando otra app.");
        } else {
          setError(`No se pudo acceder al micrófono: ${err.name}`);
        }
        continuousModeRef.current = false;
        setContinuousMode(false);
      });
  }

  function toggleRecording() {
    if (recording) {
      mediaRecorderRef.current?.stop();
      return;
    }
    startListening(false);
  }

  function toggleContinuousMode(next: boolean) {
    continuousModeRef.current = next;
    setContinuousMode(next);
    if (next) {
      startListening(true);
    } else if (mediaRecorderRef.current?.state === "recording") {
      mediaRecorderRef.current.stop();
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-4 text-xs text-slate-500">
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={voiceReplyEnabled}
            onChange={(e) => setVoiceReplyEnabled(e.target.checked)}
          />
          Leer las respuestas en voz alta
        </label>
        {micSupported && (
          <label className="flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={continuousMode}
              onChange={(e) => toggleContinuousMode(e.target.checked)}
            />
            🔁 Conversación continua (manos libres)
          </label>
        )}
        {!micSupported && (
          <span className="text-amber-500/80">
            Tu navegador no soporta grabación de audio (prueba con Chrome/Edge/Brave/Firefox recientes).
          </span>
        )}
      </div>
      {continuousMode && (
        <p className="text-xs text-slate-500">
          Habla cuando quieras, Jarvis escucha solo y sigue el turno automáticamente. Si el navegador
          bloquea la reproducción de una respuesta, aparecerá el botón de play para esa vez.
        </p>
      )}

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
                : "self-start flex flex-col gap-1.5"
            }
          >
            <div
              className={
                m.role === "user"
                  ? undefined
                  : "rounded-lg bg-black/30 px-3 py-2 text-sm text-slate-200"
              }
            >
              {m.content}
            </div>
            {/* Pedido real de usuario: "muestrame el partido X" -- Jarvis no
                controla la navegacion, pero enlaza a los partidos que sus
                herramientas ya encontraron de verdad (nunca inventados). */}
            {m.matches && m.matches.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {m.matches.map((match) => (
                  <Link
                    key={match.match_id}
                    href={`/matches/${match.match_id}`}
                    className="rounded border border-surface-border px-2 py-1 text-xs text-slate-300 hover:bg-surface-raised"
                  >
                    Ver partido: {match.home_team} vs {match.away_team} →
                  </Link>
                ))}
              </div>
            )}
          </div>
        ))}
        {transcribing && <p className="text-xs text-slate-500">Transcribiendo tu voz…</p>}
        {loading && <p className="text-xs text-slate-500">Jarvis está pensando…</p>}
        {blockedAudio && (
          // Solo aparece si el navegador bloqueo el autoplay -- el audio
          // ya esta generado, un click real (gesto de usuario) basta para
          // reproducirlo.
          <button
            onClick={() => {
              blockedAudio.play();
              setBlockedAudio(null);
            }}
            className="self-start rounded border border-surface-border px-3 py-1.5 text-xs text-slate-200 hover:bg-surface-raised"
          >
            ▶️ Reproducir respuesta
          </button>
        )}
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
        {micSupported && !continuousMode && (
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
        {continuousMode && (
          <span
            className={
              "flex items-center rounded border px-3 py-2 text-sm " +
              (recording
                ? "animate-pulse border-red-500/50 bg-red-500/15 text-red-300"
                : "border-surface-border text-slate-400")
            }
          >
            {recording ? "🎙️ Escuchando…" : "🔁 En espera…"}
          </span>
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
