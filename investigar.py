"""Investigador multi-agente.

El orquestador (agente líder):
  1. PLANEA: divide la pregunta en subtemas independientes.
  2. DELEGA: lanza un subagente por subtema, todos EN PARALELO.
  3. REVISA: lee los informes y decide si faltan cosas; si faltan, lanza otra ronda.
  4. SINTETIZA: escribe el reporte final citando las fuentes de todos los subagentes.
"""

import argparse
import datetime
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import anthropic

from llm import MODELO, MODELO_SUBAGENTE, ErrorLLM, crear_mensaje, pedir_json, resumen_uso, texto
from subagente import Hallazgo, investigar

ESQUEMA_SUBTEMAS = {
    "type": "object",
    "properties": {
        "subtemas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"titulo": {"type": "string"}, "enfoque": {"type": "string"}},
                "required": ["titulo", "enfoque"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["subtemas"],
    "additionalProperties": False,
}

ESQUEMA_REVISION = {
    "type": "object",
    "properties": {
        "suficiente": {"type": "boolean"},
        "razon": {"type": "string"},
        "huecos": ESQUEMA_SUBTEMAS["properties"]["subtemas"],
    },
    "required": ["suficiente", "razon", "huecos"],
    "additionalProperties": False,
}

SYSTEM_SINTESIS = """Eres el investigador líder. Escribes un reporte final a partir de los informes de tus subagentes.

- Estructura: título, resumen ejecutivo (3-5 viñetas), secciones por tema y una conclusión.
- Cita cada dato con el número de su fuente entre corchetes, por ejemplo [3]. Usa solo los números de la lista de fuentes.
- Si los subagentes se contradicen o algo quedó sin confirmar, dilo explícitamente.
- No agregues datos que no estén en los informes. Escribe en Markdown y en español.
- No incluyas la lista de fuentes al final: se agrega automáticamente."""


def planear(pregunta: str, n: int) -> list[dict]:
    prompt = (
        f"Pregunta de investigación: {pregunta}\n\n"
        f"Divide la investigación en {n} subtemas que se puedan investigar por separado y en paralelo, "
        "sin solaparse. Para cada uno da un título corto y un enfoque concreto: qué datos buscar y qué tipo de fuentes."
    )
    return pedir_json(prompt, ESQUEMA_SUBTEMAS)["subtemas"][:n]


def lanzar_subagentes(pregunta: str, subtemas: list[dict]) -> list[Hallazgo]:
    """Fan-out: un hilo por subagente. Cada uno trabaja en su propio contexto."""
    hallazgos = []
    with ThreadPoolExecutor(max_workers=len(subtemas)) as pool:
        futuros = {pool.submit(investigar, pregunta, s["titulo"], s["enfoque"]): s for s in subtemas}
        for futuro in as_completed(futuros):
            h = futuro.result()
            if h.error:
                print(f"   ✗ {h.titulo}: {h.error}")
            else:
                print(f"   ✓ {h.titulo}: {len(h.busquedas)} búsquedas, {len(h.fuentes)} fuentes")
            hallazgos.append(h)
    return hallazgos


def numerar_fuentes(hallazgos: list[Hallazgo]) -> list[dict]:
    """Lista global de fuentes sin duplicados, para que el reporte cite [1], [2]... de forma consistente."""
    fuentes, por_url = [], {}
    for h in hallazgos:
        for f in h.fuentes:
            if f["url"] not in por_url:
                por_url[f["url"]] = len(fuentes) + 1
                fuentes.append({**f, "numero": por_url[f["url"]]})
    return fuentes


def informes_para_el_lider(hallazgos: list[Hallazgo], fuentes: list[dict]) -> str:
    global_por_url = {f["url"]: f for f in fuentes}
    partes = []
    for h in hallazgos:
        if h.error:
            continue
        refs = ", ".join(
            f"[{global_por_url[f['url']]['numero']}] {global_por_url[f['url']]['titulo']}" for f in h.fuentes
        ) or "sin fuentes"
        partes.append(f'<informe subtema="{h.titulo}">\n{h.informe}\n\nFuentes de este informe: {refs}\n</informe>')
    return "\n\n".join(partes)


def revisar(pregunta: str, informes: str, max_huecos: int) -> dict:
    prompt = (
        f"{informes}\n\nPregunta de investigación: {pregunta}\n\n"
        "¿Estos informes alcanzan para responder la pregunta de forma completa y confiable? "
        f"Si no, indica hasta {max_huecos} huecos concretos para investigar en una nueva ronda "
        "(sin repetir lo ya cubierto). Si alcanzan, devuelve la lista de huecos vacía."
    )
    revision = pedir_json(prompt, ESQUEMA_REVISION)
    revision["huecos"] = revision["huecos"][:max_huecos]
    return revision


def sintetizar(pregunta: str, informes: str, fuentes: list[dict]) -> tuple[str, list[int]]:
    lista = "\n".join(f"[{f['numero']}] {f['titulo']} - {f['url']}" for f in fuentes)
    resp = crear_mensaje(
        max_tokens=64000,
        system=SYSTEM_SINTESIS,
        output_config={"effort": "high"},
        messages=[{
            "role": "user",
            "content": f"{informes}\n\n<fuentes>\n{lista}\n</fuentes>\n\nPregunta de investigación: {pregunta}",
        }],
    )
    reporte = texto(resp).strip()
    citadas = {int(n) for n in re.findall(r"\[(\d+)\]", reporte)}
    invalidas = sorted(n for n in citadas if not 1 <= n <= len(fuentes))
    return reporte, invalidas


def nombre_archivo(pregunta: str) -> str:
    base = unicodedata.normalize("NFKD", pregunta.lower()).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:60] or "reporte"
    return f"{datetime.date.today().isoformat()}-{slug}.md"


def main() -> int:
    parser = argparse.ArgumentParser(description="Investigación multi-agente con búsqueda web.")
    parser.add_argument("pregunta", help="Lo que quieres investigar")
    parser.add_argument("--subagentes", type=int, default=3, help="Subagentes en la primera ronda (1-6)")
    parser.add_argument("--rondas", type=int, default=2, help="Rondas máximas de investigación (1 = sin revisión)")
    parser.add_argument("--salida", type=Path, default=Path("reportes"))
    args = parser.parse_args()
    n = max(1, min(6, args.subagentes))

    print(f"Líder: {MODELO} | Subagentes: {MODELO_SUBAGENTE}\n")
    try:
        print("1) Planeando subtemas...")
        subtemas = planear(args.pregunta, n)
        for s in subtemas:
            print(f"   - {s['titulo']}: {s['enfoque']}")

        hallazgos: list[Hallazgo] = []
        for ronda in range(1, args.rondas + 1):
            print(f"\n2) Ronda {ronda}: {len(subtemas)} subagentes investigando en paralelo...")
            hallazgos += lanzar_subagentes(args.pregunta, subtemas)
            if not any(not h.error for h in hallazgos):
                print("\nNingún subagente pudo investigar. Revisa tu API key y que la búsqueda web esté habilitada.")
                return 1
            if ronda == args.rondas:
                break
            print("\n3) El líder revisa si falta información...")
            fuentes = numerar_fuentes(hallazgos)
            revision = revisar(args.pregunta, informes_para_el_lider(hallazgos, fuentes), max_huecos=2)
            print(f"   {revision['razon']}")
            if revision["suficiente"] or not revision["huecos"]:
                break
            subtemas = revision["huecos"]
            for s in subtemas:
                print(f"   - Nuevo subtema: {s['titulo']}")

        print("\n4) Sintetizando el reporte final...")
        fuentes = numerar_fuentes(hallazgos)
        reporte, invalidas = sintetizar(args.pregunta, informes_para_el_lider(hallazgos, fuentes), fuentes)
    except ErrorLLM as e:
        print(f"\nLa investigación se detuvo: {e}")
        return 1
    except anthropic.APIError as e:
        print(f"\nError de la API de Claude: {e}")
        return 1

    lista = "\n".join(f"{f['numero']}. [{f['titulo']}]({f['url']})" for f in fuentes)
    documento = f"{reporte}\n\n## Fuentes\n\n{lista}\n"
    args.salida.mkdir(parents=True, exist_ok=True)
    destino = args.salida / nombre_archivo(args.pregunta)
    destino.write_text(documento, encoding="utf-8")

    print(f"\n{reporte}\n")
    if invalidas:
        print(f"Aviso: el reporte cita fuentes que no existen: {invalidas}")
    print(f"Reporte guardado en {destino}")
    print(f"\nUso:\n{resumen_uso()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
