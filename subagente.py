"""Subagente investigador.

Cada subagente recibe UN subtema, busca en la web con la herramienta de búsqueda
del servidor de Anthropic (no hay que implementar nada: la búsqueda corre en sus
servidores) y devuelve un informe corto con sus fuentes.

Clave del patrón multi-agente: cada subagente tiene su PROPIA ventana de contexto.
Lee muchas páginas, pero al orquestador solo le llega un resumen comprimido.
"""

from dataclasses import dataclass, field

import anthropic

from llm import MODELO_SUBAGENTE, ErrorLLM, crear_mensaje, texto

MAX_BUSQUEDAS = 5
MAX_CONTINUACIONES = 4

SYSTEM = """Eres un subagente de investigación. Investigas un único subtema dentro de una investigación más grande.

- Haz entre 2 y 5 búsquedas: empieza amplio y luego afina según lo que encuentres.
- Prioriza fuentes primarias y recientes (sitios oficiales, documentación, estudios) sobre blogs o agregadores.
- Escribe un informe de máximo 350 palabras con hallazgos concretos: datos, cifras, fechas y nombres.
- Señala cuando las fuentes se contradicen o cuando algo no pudiste confirmar.
- No repitas la pregunta ni expliques tu proceso: entrega solo el informe, en español."""


@dataclass
class Hallazgo:
    titulo: str
    informe: str = ""
    fuentes: list[dict] = field(default_factory=list)
    busquedas: list[str] = field(default_factory=list)
    error: str = ""


def _extraer(bloques, hallazgo: Hallazgo) -> None:
    """Recorre los bloques de la respuesta: consultas hechas, resultados de búsqueda y citas del texto."""
    vistas = {f["url"] for f in hallazgo.fuentes}
    resultados = []
    for b in bloques:
        if b.type == "server_tool_use" and b.name == "web_search":
            hallazgo.busquedas.append(b.input.get("query", ""))
        elif b.type == "web_search_tool_result" and isinstance(b.content, list):
            resultados.extend({"titulo": r.title or r.url, "url": r.url} for r in b.content)
        elif b.type == "text":
            for cita in b.citations or []:
                if cita.type == "web_search_result_location" and cita.url not in vistas:
                    vistas.add(cita.url)
                    hallazgo.fuentes.append({"titulo": cita.title or cita.url, "url": cita.url})
    # Si el texto no trae citas, al menos se conservan los primeros resultados que se leyeron
    if not hallazgo.fuentes:
        for r in resultados[:5]:
            if r["url"] not in vistas:
                vistas.add(r["url"])
                hallazgo.fuentes.append(r)


def investigar(pregunta_general: str, titulo: str, enfoque: str) -> Hallazgo:
    hallazgo = Hallazgo(titulo)
    prompt = (
        f"Investigación general: {pregunta_general}\n\n"
        f"Tu subtema: {titulo}\n"
        f"Qué averiguar: {enfoque}"
    )
    mensajes = [{"role": "user", "content": prompt}]
    herramientas = [{"type": "web_search_20260209", "name": "web_search", "max_uses": MAX_BUSQUEDAS}]
    acumulado = []
    try:
        for _ in range(MAX_CONTINUACIONES):
            resp = crear_mensaje(
                modelo=MODELO_SUBAGENTE, permitir_pausa=True, max_tokens=64000,
                system=SYSTEM, tools=herramientas, messages=mensajes,
            )
            acumulado.extend(resp.content)
            if resp.stop_reason != "pause_turn":
                break
            # El servidor pausó un turno largo: se reenvía lo que lleva y continúa donde quedó
            mensajes = [mensajes[0], {"role": "assistant", "content": acumulado}]
        _extraer(acumulado, hallazgo)
        hallazgo.informe = texto(acumulado).strip() or "(el subagente no produjo un informe)"
    except (ErrorLLM, anthropic.APIError) as e:
        hallazgo.error = str(e)
    return hallazgo
