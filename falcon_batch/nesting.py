"""Calcula cuantos carteles entran en la plancha detectada y donde va cada uno,
en una grilla simple con margen desde el borde y espacio entre carteles.

Las posiciones que devuelve estan en el "marco local" de la plancha: origen
(0,0) en la esquina de la plancha correspondiente a corners_mm[0] (ver
sheet_detector.py), eje X a lo largo del lado corners_mm[0]->corners_mm[1].
gcode_generator.py se encarga de convertir esas coordenadas locales a
coordenadas reales de la mesa, aplicando la rotacion real con la que quedo
apoyada la plancha.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


class NestingError(Exception):
    pass


@dataclass
class SignPlacement:
    pending_index: int  # indice dentro de la lista de nombres pendientes que se paso
    x_in_sheet_mm: float  # esquina inferior izquierda del cartel, en marco local de la plancha
    y_in_sheet_mm: float


@dataclass
class NestingResult:
    placements: list[SignPlacement]
    columns: int
    rows: int
    capacity: int
    used_count: int
    leftover_count: int


def plan_grid_nesting(
    sheet_width_mm: float,
    sheet_height_mm: float,
    sign_width_mm: float,
    sign_height_mm: float,
    margin_mm: float,
    spacing_mm: float,
    n_pending: int,
) -> NestingResult:
    usable_w = sheet_width_mm - 2 * margin_mm
    usable_h = sheet_height_mm - 2 * margin_mm

    if usable_w < sign_width_mm or usable_h < sign_height_mm:
        raise NestingError(
            f"La plancha detectada ({sheet_width_mm:.1f} x {sheet_height_mm:.1f} mm, "
            f"{usable_w:.1f} x {usable_h:.1f} mm utiles con {margin_mm}mm de margen) "
            f"es mas chica que un cartel ({sign_width_mm} x {sign_height_mm} mm). "
            "Usa una plancha mas grande o reduce el margen en config.yaml."
        )

    cols = 1 + math.floor((usable_w - sign_width_mm) / (sign_width_mm + spacing_mm) + 1e-9)
    rows = 1 + math.floor((usable_h - sign_height_mm) / (sign_height_mm + spacing_mm) + 1e-9)
    capacity = cols * rows
    used = min(capacity, n_pending)

    placements: list[SignPlacement] = []
    idx = 0
    for r in range(rows):
        for c in range(cols):
            if idx >= used:
                break
            x = margin_mm + c * (sign_width_mm + spacing_mm)
            y = margin_mm + r * (sign_height_mm + spacing_mm)
            placements.append(SignPlacement(pending_index=idx, x_in_sheet_mm=x, y_in_sheet_mm=y))
            idx += 1

    return NestingResult(
        placements=placements,
        columns=cols,
        rows=rows,
        capacity=capacity,
        used_count=used,
        leftover_count=n_pending - used,
    )
