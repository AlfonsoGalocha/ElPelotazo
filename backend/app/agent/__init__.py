"""Capa de agente conversacional ("Jarvis"), aislada del motor de
prediccion: SOLO orquesta llamadas a herramientas que envuelven servicios
ya existentes (`prediction_service`, `round_service`, etc). El LLM nunca
calcula probabilidades ni inventa datos -- ver `agent/tools.py` y
`agent/orchestrator.py`.
"""
