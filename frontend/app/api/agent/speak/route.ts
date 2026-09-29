// Proxy server-side hacia el backend (`POST /agent/speak`). Mismo motivo
// que las otras rutas de agent/: AGENT_SHARED_SECRET nunca debe llegar al
// navegador. La respuesta es audio/wav binario -- se reenvia tal cual con
// arrayBuffer(), nunca .text(), para no corromper los bytes.
import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const AGENT_SHARED_SECRET = process.env.AGENT_SHARED_SECRET;

export async function POST(request: NextRequest) {
  if (!AGENT_SHARED_SECRET) {
    return NextResponse.json(
      {
        detail:
          "El agente no esta configurado en el frontend. Pon AGENT_SHARED_SECRET " +
          "(el MISMO valor que en el .env del backend) en el entorno del frontend.",
      },
      { status: 503 }
    );
  }

  const body = await request.text();
  const backendResponse = await fetch(`${API_URL}/agent/speak`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Agent-Key": AGENT_SHARED_SECRET,
    },
    body,
  });

  if (!backendResponse.ok) {
    const errorText = await backendResponse.text();
    return new NextResponse(errorText, {
      status: backendResponse.status,
      headers: { "Content-Type": "application/json" },
    });
  }

  const audio = await backendResponse.arrayBuffer();
  return new NextResponse(audio, {
    status: 200,
    headers: { "Content-Type": "audio/wav" },
  });
}
