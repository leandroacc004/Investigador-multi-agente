import json
import os
import threading
from collections import Counter

import anthropic

MODELO = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
MODELO_SUBAGENTE = os.environ.get("CLAUDE_MODEL_SUBAGENTE", "claude-sonnet-5-5")

# Estos modelos aceptan `fallbacks="default"`: si el modelo rechaza la petición,
# la API la reintenta automáticamente en otro modelo adecuado.
_MODELOS_CON_FALLBACK = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}

_client = None
_lock = threading.Lock()
# Uso acumulado por modelo: los subagentes corren en paralelo, por eso el lock
USO: dict[str, Counter] = {}


def cliente() -> anthropic.Anthropic:
    global _client
    with _lock:
        if _client is None:
            _client = anthropic.Anthropic()
    return _client


class ErrorLLM(Exception):
    pass


def _registrar_uso(modelo: str, resp) -> None:
    u = resp.usage
    with _lock:
        c = USO.setdefault(modelo, Counter())
        c["entrada"] += u.input_tokens + (u.cache_read_input_tokens or 0) + (u.cache_creation_input_tokens or 0)
        c["salida"] += u.output_tokens
        c["llamadas"] += 1
        if u.server_tool_use:
            c["busquedas"] += u.server_tool_use.web_search_requests or 0


def crear_mensaje(modelo: str = MODELO, permitir_pausa: bool = False, **kwargs):
    """Usa streaming: con respuestas largas (y el razonamiento del modelo) evita timeouts HTTP."""
    extra = {}
    if modelo in _MODELOS_CON_FALLBACK:
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    with cliente().beta.messages.stream(model=modelo, **extra, **kwargs) as stream:
        resp = stream.get_final_message()
    _registrar_uso(modelo, resp)
    if resp.stop_reason == "refusal":
        raise ErrorLLM("El modelo rechazó la petición.")
    if resp.stop_reason == "max_tokens":
        raise ErrorLLM("La respuesta se cortó por max_tokens.")
    if resp.stop_reason == "pause_turn" and not permitir_pausa:
        raise ErrorLLM("El turno quedó en pausa inesperadamente.")
    return resp


def texto(resp_o_bloques) -> str:
    bloques = getattr(resp_o_bloques, "content", resp_o_bloques)
    return "".join(b.text for b in bloques if b.type == "text")


def pedir_json(prompt: str, schema: dict, system: str = "", effort: str = "medium", max_tokens: int = 32000) -> dict:
    """Pide al orquestador una respuesta que cumple exactamente un JSON Schema (structured outputs)."""
    kwargs = {"system": system} if system else {}
    resp = crear_mensaje(
        max_tokens=max_tokens,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
        **kwargs,
    )
    return json.loads(texto(resp))


def resumen_uso() -> str:
    lineas = []
    for modelo, c in USO.items():
        lineas.append(
            f"  {modelo}: {c['llamadas']} llamadas, {c['entrada']:,} tokens de entrada, "
            f"{c['salida']:,} de salida, {c['busquedas']} búsquedas web"
        )
    return "\n".join(lineas)
