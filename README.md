# Investigador multi-agente

Le haces una pregunta de investigación y un **agente líder** la divide en subtemas, lanza varios **subagentes en paralelo** que buscan en la web, revisa si falta información (y si falta, lanza otra ronda) y finalmente escribe un reporte en Markdown con todas las fuentes citadas.

Es el proyecto más avanzado de la serie. Está inspirado en cómo Anthropic describe su propio sistema de investigación multi-agente ([How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system)).

## Arquitectura

```
                         pregunta
                            │
                            ▼
          ┌──────── LÍDER (claude-opus-5-5) ────────┐
          │ 1. PLANEA: divide en N subtemas          │
          └──┬──────────────┬──────────────┬─────────┘
             ▼              ▼              ▼            2. DELEGA (en paralelo)
       ┌──────────┐   ┌──────────┐   ┌──────────┐
       │subagente │   │subagente │   │subagente │      claude-sonnet-5-5
       │ busca en │   │ busca en │   │ busca en │      + web_search
       │ la web   │   │ la web   │   │ la web   │      (contexto propio)
       └────┬─────┘   └────┬─────┘   └────┬─────┘
            │ informe      │ informe      │ informe    (comprimido + fuentes)
            └──────────────┼──────────────┘
                           ▼
          ┌──────── LÍDER ──────────────────────────┐
          │ 3. REVISA: ¿falta algo? ── sí ──► nueva ronda de subagentes
          │ 4. SINTETIZA: reporte con citas [1] [2] │
          └──────────────────┬──────────────────────┘
                             ▼
                reportes/AAAA-MM-DD-pregunta.md
```

## Conceptos clave

| Concepto | Dónde verlo | Por qué importa |
|---|---|---|
| **Orquestador + subagentes** | `investigar.py` / `subagente.py` | El líder nunca busca: planea, delega y sintetiza. Los subagentes solo investigan su subtema. |
| **Paralelismo (fan-out)** | `lanzar_subagentes()` | Con `ThreadPoolExecutor` los subagentes trabajan al mismo tiempo: 3 subtemas tardan casi lo mismo que 1. |
| **Contexto separado y compresión** | `subagente.py` | Cada subagente lee muchas páginas en **su propia ventana de contexto** y devuelve un informe de ~350 palabras. Al líder le llega lo esencial, no el ruido. Esta es la razón principal para usar varios agentes. |
| **Herramienta del servidor** | `web_search_20260209` | La búsqueda web la ejecuta Anthropic: no hay que programar un buscador ni scraping. |
| **`pause_turn`** | `investigar()` en `subagente.py` | Si un turno con búsquedas se alarga, la API lo pausa; se reenvía lo acumulado y el modelo continúa donde quedó. |
| **Iteración (rondas)** | `revisar()` | El líder evalúa la cobertura y lanza subagentes solo para los huecos. Es la diferencia entre "buscar una vez" e "investigar". |
| **Fuentes trazables** | `numerar_fuentes()` | Las citas salen de los metadatos de la búsqueda (no de lo que el modelo escribe), se numeran sin duplicados y se verifica que el reporte no cite números inexistentes. |
| **Tolerancia a fallos** | `Hallazgo.error` | Si un subagente falla, los demás siguen y el reporte se arma con lo que hay. |
| **Observabilidad de costos** | `llm.py` → `USO` | Al final se muestran llamadas, tokens y búsquedas por modelo. |
| **Streaming** | `crear_mensaje()` | Todas las llamadas usan streaming: con respuestas largas evita timeouts. |

## Estructura

| Archivo | Para qué sirve |
|---|---|
| `investigar.py` | El agente líder: planear, delegar, revisar, sintetizar y guardar el reporte |
| `subagente.py` | El subagente investigador con búsqueda web, manejo de `pause_turn` y extracción de fuentes |
| `llm.py` | Cliente de Claude con streaming, structured outputs y contador de uso |

## Requisitos

- Python 3.10 o superior.
- Una API key de Anthropic ([console.anthropic.com](https://console.anthropic.com)).
- **La búsqueda web habilitada** para tu organización en la Console de Anthropic (la activa un administrador en la configuración de privacidad). Si no está activa, los subagentes fallan con un error de la API.

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY="tu-api-key"   # Windows: set ANTHROPIC_API_KEY=tu-api-key
```

## Uso

```bash
python investigar.py "¿Cómo evolucionó la energía solar en Chile entre 2015 y 2025?"

# Más subagentes y una sola ronda (sin revisión)
python investigar.py "Comparativa de frameworks de agentes de IA en Python" --subagentes 5 --rondas 1
```

Salida de ejemplo (resumida):

```
Líder: claude-opus-5-5 | Subagentes: claude-sonnet-5-5

1) Planeando subtemas...
   - Capacidad instalada: evolución de MW instalados por año y regiones principales
   - Política y regulación: leyes, licitaciones y metas de energía renovable
   - Costos y precios: evolución del costo por MWh y precios de licitaciones

2) Ronda 1: 3 subagentes investigando en paralelo...
   ✓ Política y regulación: 4 búsquedas, 6 fuentes
   ✓ Capacidad instalada: 3 búsquedas, 5 fuentes
   ✓ Costos y precios: 5 búsquedas, 7 fuentes

3) El líder revisa si falta información...
   Falta información sobre el almacenamiento y los recortes de generación.
   - Nuevo subtema: Almacenamiento y vertimiento

2) Ronda 2: 1 subagentes investigando en paralelo...
   ✓ Almacenamiento y vertimiento: 4 búsquedas, 5 fuentes

4) Sintetizando el reporte final...

# Energía solar en Chile: 2015-2025
...
Reporte guardado en reportes/2026-10-19-como-evoluciono-la-energia-solar-en-chile-entre-2015-y-2025.md

Uso:
  claude-opus-5-5: 4 llamadas, ...
  claude-sonnet-5-5: 4 llamadas, ..., 16 búsquedas web
```

## Costos

Una investigación usa varias llamadas al modelo líder, una o más por subagente y cada búsqueda web tiene un costo propio además de los tokens (revisa los [precios vigentes](https://www.anthropic.com/pricing)). Para controlar el gasto:

- Baja `--subagentes` o usa `--rondas 1`.
- Baja `MAX_BUSQUEDAS` en `subagente.py`.
- Usa un modelo más barato para los subagentes con `CLAUDE_MODEL_SUBAGENTE=claude-haiku-5-5`.

## Configuración

| Variable | Valor por defecto | Descripción |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Tu API key (obligatoria) |
| `CLAUDE_MODEL` | `claude-opus-5-5` | Modelo del líder (planear, revisar, sintetizar) |
| `CLAUDE_MODEL_SUBAGENTE` | `claude-sonnet-5-5` | Modelo de los subagentes |

## Ideas para seguir aprendiendo

1. **Lectura profunda:** agrega la herramienta `web_fetch_20260209` a los subagentes para que lean páginas completas, no solo resultados de búsqueda.
2. **Dominios confiables:** usa `allowed_domains` en la herramienta de búsqueda para investigaciones que deben salir de fuentes oficiales.
3. **Verificador:** agrega un subagente final que revise cada afirmación del reporte contra su fuente citada.
4. **Fuentes locales:** combina este proyecto con *RAG multi-fuente* y dale a un subagente acceso a tus documentos internos.
5. **Managed Agents:** compara esta implementación con los [Managed Agents de Anthropic](https://docs.claude.com/), donde Anthropic ejecuta el loop y la orquestación por ti.
