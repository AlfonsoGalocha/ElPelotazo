// Proxy server-side hacia el backend (`POST /agent/chat`). Existe SOLO
// para que el secreto AGENT_SHARED_SECRET nunca llegue al navegador: si
// el frontend llamara directamente al backend desde el cliente, ese
// secreto tendria que ir en una variable NEXT_PUBLIC_* (visible para
// cualquiera que abra las herramientas de desarrollador de la pagina),
// lo que anularia el proposito de tenerlo. Esta ruta corre en el
// servidor de Next.js, donde SI se puede leer una variable de entorno
// normal (sin prefijo NEXT_PUBLIC_) de forma segura.
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
  const backendResponse = await fetch(`${API_URL}/agent/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Agent-Key": AGENT_SHARED_SECRET,
    },
    body,
  });

  const data = await backendResponse.text();
  return new NextResponse(data, {
    status: backendResponse.status,
    headers: { "Content-Type": "application/json" },
  });
}
