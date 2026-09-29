// Proxy server-side hacia el backend (`POST /agent/transcribe`). Mismo
// motivo que app/api/agent/chat/route.ts: AGENT_SHARED_SECRET nunca debe
// llegar al navegador. El body (multipart/form-data con el audio grabado
// por MediaRecorder) se reenvia tal cual -- no se puede usar
// `request.text()` aqui porque corromperia los bytes binarios del audio.
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

  const formData = await request.formData();
  const backendResponse = await fetch(`${API_URL}/agent/transcribe`, {
    method: "POST",
    headers: { "X-Agent-Key": AGENT_SHARED_SECRET },
    body: formData,
  });

  const data = await backendResponse.text();
  return new NextResponse(data, {
    status: backendResponse.status,
    headers: { "Content-Type": "application/json" },
  });
}
