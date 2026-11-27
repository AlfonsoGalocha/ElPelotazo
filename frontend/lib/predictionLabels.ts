// Etiquetas compartidas entre "Mejores oportunidades" (top-signals) y el
// detalle de señal/selector (signals/[id]) -- centralizadas aquí para no
// mantener dos copias que puedan divergir. Ver backend/app/prediction/
// ranking.py::ExclusionReason para los códigos de exclusión y
// backend/app/services/evaluation_service.py::TrackRecordEstimate para
// los niveles de fiabilidad.
export const EXCLUSION_LABELS: Record<string, string> = {
  ventaja_extrema_frente_al_mercado: "Revisar: el modelo supera al mercado en más de 25 puntos",
  sin_mercado: "Sin cuota para ese mercado",
  cuota_fuera_de_banda: "Fuera del rango de la estrategia",
  cuota_desactualizada: "Cuota desactualizada",
  origen_cuota_no_verificado: "Hora de la cuota sin verificar",
  calibracion_no_disponible: "Probabilidad sin calibrar",
  calibracion_en_extremo: "Calibración en 0 % o 100 % pendiente de validar",
  ajuste_calibracion_sin_respaldo: "Ajuste de calibración superior a 15 puntos sin evidencia de la liga y mercado",
  incertidumbre_elevada_o_no_disponible: "Intervalo amplio o no disponible",
  cuota_sin_frescura_alta: "Cuota fuera del límite de alta confianza",
  incertidumbre_no_disponible: "Incertidumbre sin estimar",
  ev_estimado_no_positivo: "Sin ventaja estimada a esa cuota",
  ev_conservador_no_positivo: "Sin ventaja con la estimación conservadora",
  fiabilidad_historica_insuficiente: "Evidencia histórica insuficiente",
  cuota_invalida: "Cuota inválida",
  cuota_demasiado_alta: "Cuota por encima del máximo permitido",
  edge_invalido: "Edge ausente o por debajo del mínimo",
  pocas_casas: "Respaldada por muy pocas casas",
  calidad_datos_baja: "Calidad de datos por debajo del mínimo",
  contradiccion_fuerte: "Contradicción fuerte con la frecuencia histórica",
};

export function exclusionLabel(reason: string): string {
  return EXCLUSION_LABELS[reason] ?? reason.replaceAll("_", " ");
}

// Mision "oportunidades" seccion 2.2 (fiabilidad jerarquica): nunca
// mostrar un nivel "market_tier"/"market_global" como si fuera especifico
// de esta competicion exacta -- la etiqueta deja claro de donde sale.
export const RELIABILITY_SOURCE_LABELS: Record<string, string> = {
  competition_market: "esta liga",
  // Bloque 2.2 de la mision (shrinkage bayesiano, ver
  // evaluation_service.py::TRACK_RECORD_SHRINKAGE_K): mezcla de poca
  // muestra de esta liga con un respaldo fiable -- nunca se presenta
  // como igual de especifica que "esta liga" puro.
  competition_market_shrunk: "esta liga (muestra pequeña, ajustada)",
  market_tier: "ligas similares",
  market_global: "global",
};
