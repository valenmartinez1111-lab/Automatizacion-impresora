"""Agente de vision: revisa la foto de la plancha antes de dejar prender el laser.

Este es el "segundo par de ojos" que pediste: no alcanza con que la deteccion
geometrica (sheet_detector.py) encuentre un rectangulo prolijo por diferencia de
fondo, porque esa deteccion puede confundirse (una sombra dura, una herramienta
olvidada sobre la mesa, la plancha cortada a la mitad por el borde de la camara,
etc.). Antes de cada trabajo, se le manda a Claude la foto real + un dibujo de lo
que el algoritmo geometrico detecto como plancha, y tiene que confirmar
explicitamente que tiene sentido o frenar el proceso.

Este chequeo es un requisito para poder llamar a la tool que envia G-code
(ver tools.py): sin una verificacion aprobada no hay forma de mandar nada a la
maquina.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass

import anthropic

from .config import VisionAgentConfig
from .sheet_detector import SheetDetection

_TOOL_NAME = "report_verification"

_SYSTEM_PROMPT = """\
Sos un agente de seguridad para una cortadora/grabadora laser (Creality Falcon2 Pro S).
Tu unica tarea es revisar una foto de la mesa de trabajo antes de que el sistema envie
un trabajo de grabado/corte, y decidir si es seguro proceder.

Se te muestra una foto de la mesa con una plancha de material apoyada, con un rectangulo
rojo superpuesto que indica donde un algoritmo geometrico (por diferencia de fondo)
detecto el borde de la plancha, y sus 4 esquinas etiquetadas con coordenadas en mm.
La foto puede tener distorsion tipo "ojo de pez" en los bordes (la camara de esta
maquina es una lente gran angular fija); eso es normal y no es un problema en si mismo.

Aprobá (approved=true) SOLO si todo esto se cumple:
- El rectangulo rojo coincide razonablemente con los bordes reales y visibles de la
  plancha de material en la foto (tolerancia normal: unos pocos mm/grados, no metros
  ni un rectangulo en el aire o sobre una zona vacia de la mesa).
- La plancha es una sola pieza, apoyada plana sobre la mesa (no doblada, no apilada,
  no de canto).
- No hay manos, herramientas, cables sueltos, ni otros objetos dentro o cerca del
  rectangulo detectado que puedan interferir con el cabezal laser.
- No hay nada que sugiera un riesgo de incendio evidente (por ejemplo restos de
  materiales quemados o inflamables fuera del area de trabajo esperada).

Si algo no se ve con claridad en la foto, es mejor rechazar (approved=false) y explicar
por que, en vez de asumir que esta bien.

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
    debug_image_path: str,
    detection: SheetDetection,
    client: anthropic.Anthropic | None = None,
) -> VerificationResult:
    """Le pide a Claude que confirme (o rechace) la deteccion geometrica de la plancha."""
    client = client or anthropic.Anthropic()

    img_b64, media_type = _load_image_b64(debug_image_path)

    user_text = (
        f"Deteccion geometrica: plancha de {detection.width_mm:.1f} x "
        f"{detection.height_mm:.1f} mm, rotada {detection.rotation_deg:.1f} grados, "
        f"centro en ({detection.center_mm[0]:.1f}, {detection.center_mm[1]:.1f}) mm, "
        f"rectangularidad {detection.rectangularity:.2f} "
        f"(1.0 = rectangulo perfecto). Revisa la foto y confirma si esto es correcto "
        "y seguro para proceder."
    )

    resp = client.messages.create(
        model=cfg.model,
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        tools=_TOOLS,
        tool_choice={"type": "tool", "name": _TOOL_NAME},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": img_b64},
                    },
                    {"type": "text", "text": user_text},
                ],
            }
        ],
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
