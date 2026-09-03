"""Agente orquestador: usa Claude (tool-use) para decidir el flujo de trabajo
del grabado en serie, llamando a las tools de tools.py en el orden correcto.

Por que un agente y no un for-loop fijo: la secuencia real tiene pasos que
dependen del resultado del paso anterior de forma que no siempre es lineal
(una verificacion de vision rechazada puede requerir volver a sacar la foto,
una plancha puede no alcanzar para ningun cartel y hay que pedir una mas
grande, una alarma de la maquina requiere frenar y avisar en vez de
reintentar solo). El agente decide que hacer en cada paso; las tools (ver
tools.py) son las que garantizan que, decida lo que decida, nunca se saltee
la verificacion de seguridad antes de mover el laser.
"""
from __future__ import annotations

import json

import anthropic

from .config import OrchestratorAgentConfig
from .tools import BatchToolbox, ToolError

_TOOL_DEFS = [
    {
        "name": "get_pending_names",
        "description": "Devuelve la lista de nombres pendientes, ya hechos, y fallidos.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ask_human",
        "description": (
            "Le muestra una pregunta o instruccion a la persona frente a la maquina "
            "y espera su respuesta por consola. Usalo para pedir que coloque una "
            "plancha nueva, que confirme que la mesa esta vacia, o para avisar de "
            "un problema que necesita intervencion humana."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "capture_reference_photo",
        "description": (
            "Saca una foto de referencia de la mesa. Se DEBE llamar con la mesa "
            "vacia (confirmado por el humano), antes de colocar el material."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "capture_current_photo",
        "description": "Saca una foto de la mesa con el material ya colocado.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "detect_sheet_on_table",
        "description": (
            "Compara la foto de referencia con la foto actual para detectar el "
            "rectangulo de la plancha de material (posicion, tamano, rotacion). "
            "Requiere haber capturado ambas fotos antes."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "verify_sheet_with_vision",
        "description": (
            "Verificacion de seguridad OBLIGATORIA: le pide a un modelo de vision "
            "que confirme que la deteccion geometrica de la plancha es correcta y "
            "segura antes de generar o enviar ningun G-code. Sin esto aprobado, "
            "generate_job_gcode y send_job_to_printer van a fallar."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "plan_nesting_for_pending",
        "description": (
            "Calcula cuantos carteles de la plantilla configurada entran en la "
            "plancha detectada, en una grilla, y cuales de los nombres pendientes "
            "van en este trabajo."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "generate_job_gcode",
        "description": (
            "Genera el G-code del trabajo (los carteles que entraron segun "
            "plan_nesting_for_pending), ya transformado a la posicion y rotacion "
            "reales de la plancha detectada."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "send_job_to_printer",
        "description": (
            "Envia el G-code generado a la maquina y espera a que termine. Esto "
            "prende el laser de verdad. Falla si no hay una verificacion de vision "
            "aprobada vigente para la plancha actual."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_printer_status",
        "description": "Consulta el estado actual de la maquina (Idle, Run, Alarm, etc).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "emergency_stop",
        "description": (
            "Frena la maquina (feed hold + soft reset). Usalo si algo se ve mal "
            "y hay que detener el trabajo en curso ya mismo."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "log_note",
        "description": "Deja una nota breve en el log del proceso, para trazabilidad.",
        "input_schema": {
            "type": "object",
            "properties": {"note": {"type": "string"}},
            "required": ["note"],
        },
    },
]

_SYSTEM_PROMPT = """\
Sos el agente que orquesta un grabado laser en serie en una Creality Falcon2 Pro S,
usando las tools disponibles. Tu objetivo: procesar todos los nombres pendientes,
grabandolos en planchas de material que una persona va colocando sobre la mesa.

Flujo esperado para CADA plancha nueva:
 1. ask_human para confirmar que la mesa esta vacia (o pedir que la vacien).
 2. capture_reference_photo (mesa vacia).
 3. ask_human para pedir que coloquen la plancha de material.
 4. capture_current_photo.
 5. detect_sheet_on_table.
 6. verify_sheet_with_vision. Si NO aprueba: usa ask_human para explicar el
    problema (segun 'reason'/'concerns') y pedir que se corrija (acomodar la
    plancha, sacar algo de la mesa, etc.), y volve a intentar desde el paso 3 o 4
    segun corresponda. No sigas adelante sin aprobacion.
 7. plan_nesting_for_pending. Si la plancha no entra ni un cartel, avisa con
    ask_human y pedi una plancha mas grande.
 8. generate_job_gcode.
 9. get_printer_status: confirma que este Idle antes de enviar.
 10. send_job_to_printer.
 11. Si quedan nombres pendientes (get_pending_names), repeti desde el paso 1
     para la proxima plancha. Si no queda ninguno, terminá y resumí el resultado.

Reglas duras (no negociables):
 - Nunca llames a send_job_to_printer sin haber pasado por verify_sheet_with_vision
   aprobado para la deteccion actual (la tool lo va a rechazar igual, pero no lo
   intentes).
 - Si algo sale mal de forma que no sabes resolver con las tools disponibles
   (un error repetido, una alarma de la maquina, una respuesta ambigua del
   humano), usa emergency_stop si la maquina puede estar en movimiento, explica
   la situacion con texto y terminá tu turno: no insistas en bucle.
 - Se conciso con el humano: preguntas y avisos claros, sin explicaciones largas.

Cuando ya no queden nombres pendientes (o decidas frenar por un problema),
terminá con un mensaje de texto plano (sin tool call) resumiendo que se hizo.
"""


class BatchAgentError(Exception):
    pass


def _dispatch(toolbox: BatchToolbox, name: str, tool_input: dict) -> dict:
    fn = {
        "get_pending_names": toolbox.get_pending_names,
        "ask_human": lambda: toolbox.ask_human(tool_input["question"]),
        "capture_reference_photo": toolbox.capture_reference_photo,
        "capture_current_photo": toolbox.capture_current_photo,
        "detect_sheet_on_table": toolbox.detect_sheet_on_table,
        "verify_sheet_with_vision": toolbox.verify_sheet_with_vision,
        "plan_nesting_for_pending": toolbox.plan_nesting_for_pending,
        "generate_job_gcode": toolbox.generate_job_gcode,
        "send_job_to_printer": toolbox.send_job_to_printer,
        "get_printer_status": toolbox.get_printer_status,
        "emergency_stop": toolbox.emergency_stop,
        "log_note": lambda: toolbox.log_note(tool_input["note"]),
    }.get(name)
    if fn is None:
        raise ToolError(f"Tool desconocida: {name}")
    return fn()


def run_batch_agent(
    cfg: OrchestratorAgentConfig,
    toolbox: BatchToolbox,
    client: anthropic.Anthropic | None = None,
) -> None:
    client = client or anthropic.Anthropic()

    pending = toolbox.get_pending_names()["pending"]
    user_msg = (
        f"Plantilla de cartel: '{toolbox.template.name}' "
        f"({toolbox.template.width_mm}x{toolbox.template.height_mm}mm).\n"
        f"Nombres pendientes ({len(pending)}): {json.dumps(pending, ensure_ascii=False)}\n\n"
        "Arranca el proceso."
    )
    messages: list[dict] = [{"role": "user", "content": user_msg}]

    for iteration in range(cfg.max_tool_iterations):
        resp = client.messages.create(
            model=cfg.model,
            max_tokens=2048,
            system=_SYSTEM_PROMPT,
            tools=_TOOL_DEFS,
            messages=messages,
        )

        messages.append({"role": "assistant", "content": resp.content})

        text_parts = [b.text for b in resp.content if b.type == "text"]
        for t in text_parts:
            if t.strip():
                print(f"\n[AGENTE] {t.strip()}")

        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            # el agente termino (mensaje de texto plano, sin tool calls)
            return

        tool_results = []
        for tu in tool_uses:
            try:
                result = _dispatch(toolbox, tu.name, tu.input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            except ToolError as exc:
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": f"ERROR: {exc}",
                        "is_error": True,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - se lo mostramos al agente para que reaccione
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": f"ERROR INESPERADO: {exc}",
                        "is_error": True,
                    }
                )

        messages.append({"role": "user", "content": tool_results})

    raise BatchAgentError(
        f"Se alcanzo el limite de {cfg.max_tool_iterations} pasos del agente sin terminar. "
        "Revisa logs/job_log.jsonl y el estado de la maquina manualmente."
    )
