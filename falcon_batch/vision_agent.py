"""Agente de vision: revisa las fotos del escaneo antes de dejar prender el laser.

Este es el "segundo par de ojos" que pediste: no alcanza con que la deteccion
geometrica (grid_scan.py + sheet_detector.py) ajuste un rectangulo prolijo a
partir de los parches que cambiaron entre el escaneo de referencia y el
escaneo con material, porque esa deteccion puede confundirse (una sombra
dura, una herramienta olvidada sobre la mesa, un reflejo). Como la camara de
la Falcon2 esta montada en el cabezal, no hay una sola foto global de toda
la plancha para mostrar -- en su lugar, se le mandan a Claude las fotos de
cada parche de la grilla donde se detecto un cambio (con la zona marcada en
rojo), y tiene que confirmar que, en conjunto, tiene sentido como una sola
plancha de material bien apoyada.

Este chequeo es un requisito para poder llamar a la tool que envia G-code
(ver tools.py): sin una verificacion aprobada no hay forma de mandar nada a
la maquina.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass

import anthropic

from .config import VisionAgentConfig
from .sheet_detector import SheetDetection

_TOOL_NAME = "report_verification"
MAX_IMAGES = 12

_SYSTEM_PROMPT = """\
Sos un agente de seguridad para una cortadora/grabadora laser (Creality Falcon2 Pro S).
Tu unica tarea es revisar fotos de la mesa de trabajo antes de que el sistema envie un
trabajo de grabado/corte, y decidir si es seguro proceder.

Como la camara esta montada en el cabezal de la maquina, no hay una sola foto de toda
la mesa: en su lugar se te muestran varias fotos, cada una de un parche chico de la
mesa (tomadas moviendo el cabezal en una grilla), donde un algoritmo geometrico detecto
un cambio entre la mesa vacia y la mesa con material -- esa zona de cambio esta resaltada
en rojo semitransparente sobre la foto. Cada imagen indica en que posicion (x,y) de la
mesa, en mm, se tomo.

Ademas se te da un resumen del rectangulo que el algoritmo geometrico ajusto a partir de
TODOS los parches con cambio (tamano, centro, rotacion, y que tan 'rectangular' salio el
ajuste).

Aprobá (approved=true) SOLO si todo esto se cumple, mirando el conjunto de fotos:
- Las zonas rojas en las distintas fotos son consistentes con los bordes de UNA sola
  plancha de material continua (no varios objetos sueltos, no manchas dispersas sin
  relacion entre si).
- La plancha se ve apoyada plana sobre la mesa (no doblada, no apilada, no de canto).
- No hay manos, herramientas, cables sueltos, ni otros objetos ademas del material en
  ninguna de las fotos.
- El tamano/centro/rotacion del rectangulo ajustado es consistente con lo que se ve en
  las fotos (no es un rectangulo mucho mas grande o chico, o en otro lugar, que lo que
  las fotos muestran).
- No hay nada que sugiera un riesgo de incendio evidente.

Si algo no se ve con claridad, o las fotos parecen contradictorias entre si, es mejor
rechazar (approved=false) y explicar por que, en vez de asumir que esta bien.

Respondé siempre usando la tool report_verification.
"""

_TOOLS = [
    {
        "name": _TOOL_NAME,
        "description": "Reporta el veredicto de la verificacion de seguridad de la plancha.",
        "input_schema": {
            "type": "object",
            "properties": {
                "approved": {
                    "type": "boolean",
                    "description": "true solo si es seguro proceder con el trabajo de laser.",
                },
                "confidence": {
                    "type": "number",
                    "description": "Confianza en el veredicto, de 0.0 a 1.0.",
                },
                "reason": {
                    "type": "string",
                    "description": "Explicacion breve y concreta del veredicto, en espanol.",
                },
                "concerns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de problemas puntuales observados, si los hay.",
                },
            },
            "required": ["approved", "confidence", "reason"],
        },
    }
]


class VisionAgentError(Exception):
    pass


@dataclass
class VerificationResult:
    approved: bool
    confidence: float
    reason: str
    concerns: list[str]
    verification_token: str | None
    """Token opaco que prueba que esta verificacion paso. Lo exige send_gcode() en tools.py."""


def _load_image_b64(path: str) -> tuple[str, str]:
    with open(path, "rb") as f:
        data = f.read()
    media_type = "image/jpeg" if path.lower().endswith((".jpg", ".jpeg")) else "image/png"
    return base64.standard_b64encode(data).decode("utf-8"), media_type


def verify_sheet(
    cfg: VisionAgentConfig,
    debug_image_paths: list[str],
    detection: SheetDetection,
    client: anthropic.Anthropic | None = None,
) -> VerificationResult:
    """Le pide a Claude que confirme (o rechace) la deteccion geometrica de la plancha,
    a partir de las fotos de los parches del escaneo donde se detecto cambio."""
    client = client or anthropic.Anthropic()

    if not debug_image_paths:
        raise VisionAgentError("No hay fotos de deteccion para verificar.")

    images_to_send = debug_image_paths[:MAX_IMAGES]

    content: list[dict] = []
    for path in images_to_send:
        img_b64, media_type = _load_image_b64(path)
        content.append(
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": img_b64}}
        )

    summary_text = (
        f"Se muestran {len(images_to_send)} de {len(debug_image_paths)} parches con cambio "
        "detectado (zona roja = cambio respecto de la mesa vacia).\n\n"
        f"Rectangulo ajustado con todos los parches: {detection.width_mm:.1f} x "
        f"{detection.height_mm:.1f} mm, rotado {detection.rotation_deg:.1f} grados, "
        f"centro en ({detection.center_mm[0]:.1f}, {detection.center_mm[1]:.1f}) mm, "
        f"rectangularidad {detection.rectangularity:.2f} (1.0 = rectangulo perfecto).\n\n"
        "Revisa las fotos y confirma si esto es correcto y seguro para proceder."
    )
    content.append({"type": "text", "text": summary_text})

    resp = client.messages.create(
        model=cfg.model,
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        tools=_TOOLS,
        tool_choice={"type": "tool", "name": _TOOL_NAME},
        messages=[{"role": "user", "content": content}],
    )

    tool_use = next((b for b in resp.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise VisionAgentError("El agente de vision no devolvio un veredicto valido.")

    data = tool_use.input
    approved = bool(data.get("approved", False)) and float(data.get("confidence", 0)) >= cfg.min_confidence

    token = None
    if approved:
        # Token trivial pero suficiente: ata la aprobacion a esta deteccion puntual
        # (dimensiones + centro + rotacion), asi no se puede reusar para otra plancha.
        token = (
            f"ok:{detection.width_mm:.1f}:{detection.height_mm:.1f}:"
            f"{detection.center_mm[0]:.1f}:{detection.center_mm[1]:.1f}:"
            f"{detection.rotation_deg:.1f}"
        )

    return VerificationResult(
        approved=approved,
        confidence=float(data.get("confidence", 0)),
        reason=str(data.get("reason", "")),
        concerns=list(data.get("concerns", [])),
        verification_token=token,
    )
