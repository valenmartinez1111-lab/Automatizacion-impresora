"""Relleno de contornos por lineas horizontales (hatch), para el modo de grabado
'fill' (letras solidas, no solo el contorno). Usa la regla par-impar (even-odd),
que resuelve solo los agujeros de letras como 'o', 'a', 'e', 'g' sin necesidad de
saber cual contorno es 'exterior' y cual es 'agujero': un punto queda adentro del
relleno si el rayo horizontal que pasa por el cruza un numero IMPAR de bordes
a su izquierda.
"""
from __future__ import annotations

Point = tuple[float, float]
Segment = tuple[Point, Point]


def scanline_fill(contours: list[list[Point]], interval_mm: float) -> list[Segment]:
    """Genera segmentos horizontales de relleno para un conjunto de contornos
    cerrados (en las mismas unidades/coordenadas), separados interval_mm entre si.

    Los contornos no necesitan repetir el primer punto al final: se tratan como
    cerrados implicitamente (el ultimo punto conecta con el primero).
    """
    all_points = [pt for contour in contours for pt in contour]
    if not all_points or interval_mm <= 0:
        return []

    y_min = min(p[1] for p in all_points)
    y_max = max(p[1] for p in all_points)

    segments: list[Segment] = []
    y = y_min + interval_mm / 2.0
    row_idx = 0
    while y <= y_max:
        xs: list[float] = []
        for contour in contours:
            n = len(contour)
            for i in range(n):
                x1, y1 = contour[i]
                x2, y2 = contour[(i + 1) % n]
                if y1 == y2:
                    continue
                if (y1 <= y < y2) or (y2 <= y < y1):
                    t = (y - y1) / (y2 - y1)
                    xs.append(x1 + t * (x2 - x1))
        xs.sort()

        pairs = [(xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2)]
        if row_idx % 2 == 1:
            # zigzag: alternar sentido para minimizar desplazamientos en vacio
            pairs = [(b, a) for (a, b) in reversed(pairs)]

        for x1, x2 in pairs:
            segments.append(((x1, y), (x2, y)))

        y += interval_mm
        row_idx += 1

    return segments
