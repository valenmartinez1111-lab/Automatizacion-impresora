"""Arma el arte vectorial de UN cartel (texto ya posicionado y centrado dentro
del rectangulo del cartel), aplicando las medidas de tu plantilla (tamano de
fuente, alto de texto, ancho maximo, interlineado).

El resultado esta en coordenadas LOCALES al cartel: origen (0,0) en la esquina
inferior izquierda del cartel, tal como se va a cortar/grabar. La ubicacion de
ese cartel dentro de la plancha real (nesting.py) y la orientacion final sobre
la mesa (sheet_detector.py) se aplican despues, en gcode_generator.py.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import SignTemplate
from .text_to_paths import FontRenderer, scale_layout_to_mm


class SignLayoutError(Exception):
    pass


@dataclass
class SignArtwork:
    text: str
    template_name: str
    polylines_mm: list[list[tuple[float, float]]]  # coords locales al cartel
    sign_width_mm: float
    sign_height_mm: float
    line_widths_mm: list[float]
    shrunk_to_fit: bool


def _split_lines(text: str, template: SignTemplate) -> list[str]:
    lines = text.split("\n")
    lines = [ln.strip() for ln in lines]
    if any(not ln for ln in lines):
        raise SignLayoutError(f"'{text}' tiene un renglon vacio.")
    if len(lines) > template.max_lines:
        raise SignLayoutError(
            f"'{text}' tiene {len(lines)} renglones pero la plantilla "
            f"'{template.name}' admite maximo {template.max_lines}."
        )
    return lines


def build_sign_artwork(
    text: str, template: SignTemplate, font_renderer: FontRenderer
) -> SignArtwork:
    lines = _split_lines(text, template)

    line_layouts = font_renderer.layout_multiline(lines)

    line_polylines: list[list[list[tuple[float, float]]]] = []
    line_widths_mm: list[float] = []
    shrunk_to_fit = False

    for layout in line_layouts:
        polylines, width_mm = scale_layout_to_mm(layout, template.text_height_mm)
        if width_mm > template.max_line_width_mm:
            scale = template.max_line_width_mm / width_mm
            polylines = [[(x * scale, y * scale) for (x, y) in c] for c in polylines]
            width_mm *= scale
            shrunk_to_fit = True
        line_polylines.append(polylines)
        line_widths_mm.append(width_mm)

    n_lines = len(lines)
    # bloque de texto centrado verticalmente en el cartel. Para 1 renglon, el
    # "centro" de la caja de mayusculas se alinea con el centro del cartel; para
    # varios renglones, se distribuyen simetricamente separados por line_spacing_mm.
    total_block_height_mm = template.text_height_mm + template.line_spacing_mm * (n_lines - 1)
    top_y = template.height_mm / 2.0 + total_block_height_mm / 2.0

    all_polylines: list[list[tuple[float, float]]] = []
    for i, (polylines, width_mm) in enumerate(zip(line_polylines, line_widths_mm)):
        baseline_y = (
            top_y - template.text_height_mm - i * template.line_spacing_mm
            if n_lines > 1
            else (template.height_mm - template.text_height_mm) / 2.0
        )
        x_offset = (template.width_mm - width_mm) / 2.0
        for contour in polylines:
            all_polylines.append([(x + x_offset, y + baseline_y) for (x, y) in contour])

    return SignArtwork(
        text=text,
        template_name=template.name,
        polylines_mm=all_polylines,
        sign_width_mm=template.width_mm,
        sign_height_mm=template.height_mm,
        line_widths_mm=line_widths_mm,
        shrunk_to_fit=shrunk_to_fit,
    )
