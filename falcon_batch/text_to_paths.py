"""Convierte texto (nombre del cartel) en polilineas vectoriales, usando la fuente
real (Arial.ttf u otra TTF) en vez de dejar que una libreria de UI la rasterice.

Esto imita lo que hace Falcon Design Space puertas adentro: usa los contornos
reales de las letras de la fuente, no un renderizado de pixeles. Las curvas
(cuadraticas de TrueType, o cubicas si el glyph las tuviera) se aplanan a
segmentos de recta con suficiente resolucion para que el laser las siga sin
que se note el poligonal.
"""
from __future__ import annotations

from dataclasses import dataclass

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont

FLATTEN_STEPS = 14  # segmentos de recta por curva; suficiente para letras de pocos mm


class FontError(Exception):
    pass


class _FlattenPen(BasePen):
    """Aplana el contorno de un glyph (quadraticas TrueType o cubicas) a polilineas."""

    def __init__(self, glyph_set, steps: int = FLATTEN_STEPS):
        super().__init__(glyph_set)
        self.steps = steps
        self.contours: list[list[tuple[float, float]]] = []
        self._current: list[tuple[float, float]] = []

    def _moveTo(self, pt):
        self._current = [pt]

    def _lineTo(self, pt):
        self._current.append(pt)

    def _curveToOne(self, pt1, pt2, pt3):
        p0 = self._current[-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps
            mt = 1 - t
            x = mt**3 * p0[0] + 3 * mt**2 * t * pt1[0] + 3 * mt * t**2 * pt2[0] + t**3 * pt3[0]
            y = mt**3 * p0[1] + 3 * mt**2 * t * pt1[1] + 3 * mt * t**2 * pt2[1] + t**3 * pt3[1]
            self._current.append((x, y))

    def _qCurveToOne(self, pt1, pt2):
        p0 = self._current[-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps
            mt = 1 - t
            x = mt**2 * p0[0] + 2 * mt * t * pt1[0] + t**2 * pt2[0]
            y = mt**2 * p0[1] + 2 * mt * t * pt1[1] + t**2 * pt2[1]
            self._current.append((x, y))

    def _closePath(self):
        if self._current:
            self.contours.append(self._current)
        self._current = []

    def _endPath(self):
        if self._current:
            self.contours.append(self._current)
        self._current = []


@dataclass
class TextLayout:
    """Contornos de una linea de texto, en unidades de fuente (escala arbitraria).

    contours: lista de polilineas cerradas (cada una, lista de (x,y)).
    width: ancho total de la linea en unidades de fuente.
    units_per_em: para convertir a mm despues (mm_por_unidad = font_size_mm / units_per_em).
    """

    contours: list[list[tuple[float, float]]]
    width: float
    units_per_em: int


class FontRenderer:
    def __init__(self, font_path: str):
        try:
            self.font = TTFont(font_path)
        except Exception as exc:  # noqa: BLE001 - queremos el mensaje original tambien
            raise FontError(f"No se pudo cargar la fuente '{font_path}': {exc}") from exc
        self.units_per_em = int(self.font["head"].unitsPerEm)
        self.glyph_set = self.font.getGlyphSet()
        self.hmtx = self.font["hmtx"]
        self.cmap = self.font.getBestCmap()

    def layout_line(self, text: str) -> TextLayout:
        """Genera los contornos de una linea de texto sin saltos de renglon."""
        if "\n" in text:
            raise FontError("layout_line no admite saltos de linea; usa layout_multiline.")

        contours: list[list[tuple[float, float]]] = []
        x_cursor = 0.0
        for ch in text:
            glyph_name = self.cmap.get(ord(ch))
            if glyph_name is None:
                raise FontError(
                    f"La fuente no tiene el caracter '{ch}' (U+{ord(ch):04X}). "
                    "Elegi otra fuente o corregi el nombre."
                )
            pen = _FlattenPen(self.glyph_set)
            self.glyph_set[glyph_name].draw(pen)
            for contour in pen.contours:
                contours.append([(x + x_cursor, y) for (x, y) in contour])

            advance = self.hmtx[glyph_name][0]
            x_cursor += advance

        return TextLayout(contours=contours, width=x_cursor, units_per_em=self.units_per_em)

    def layout_multiline(self, lines: list[str]) -> list[TextLayout]:
        return [self.layout_line(line) for line in lines]


def scale_layout_to_mm(
    layout: TextLayout, target_text_height_mm: float, cap_height_ratio: float = 0.716
) -> tuple[list[list[tuple[float, float]]], float]:
    """Escala los contornos (en unidades de fuente) para que la altura de mayuscula
    (cap height, no la altura total del em-square) mida target_text_height_mm.

    cap_height_ratio es la proporcion tipica cap-height / em para fuentes sans-serif
    tipo Arial/Liberation Sans (~0.716). Si la fuente trae la tabla OS/2 con
    sCapHeight, se podria usar ese valor exacto; el ratio por defecto es una
    aproximacion suficientemente buena para el uso en carteles.

    Devuelve (contornos_en_mm, ancho_total_mm).
    """
    cap_height_units = layout.units_per_em * cap_height_ratio
    scale = target_text_height_mm / cap_height_units

    scaled = [[(x * scale, y * scale) for (x, y) in contour] for contour in layout.contours]
    width_mm = layout.width * scale
    return scaled, width_mm
