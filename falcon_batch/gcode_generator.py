"""Convierte los carteles ya posicionados (sign_layout.py + nesting.py) en
G-code real para la Falcon2 Pro S, aplicando la transformacion geometrica de
la plancha detectada (sheet_detector.py) para que el dibujo caiga exactamente
donde esta el material fisico, con la rotacion con la que quedo apoyado.

Los chequeos de limites de la mesa se hacen ACA, punto por punto, antes de
generar una sola linea de G-code: es la ultima barrera antes de que algo se
mande al laser.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import EngraveParams, PrinterConfig
from .fill import scanline_fill
from .nesting import SignPlacement
from .sheet_detector import SheetDetection
from .sign_layout import SignArtwork

Point = tuple[float, float]


class GCodeGenerationError(Exception):
    pass


def sheet_to_bed_transform(detection: SheetDetection):
    """Devuelve una funcion (u,v) [mm, marco local de la plancha] -> (x,y) [mm, mesa].

    Se arma directamente a partir de los vectores medidos entre las esquinas
    detectadas (no se asume que esos vectores sean perfectamente perpendiculares
    ni una rotacion pura): esto evita que un texto termine reflejado/espejado si
    la camara o la homografia invierten el sentido de un eje.
    """
    origin = detection.corners_mm[0]
    ex = (detection.corners_mm[1] - origin) / detection.width_mm
    ey = (detection.corners_mm[3] - origin) / detection.height_mm

    def transform(u: float, v: float) -> Point:
        x = origin[0] + u * ex[0] + v * ey[0]
        y = origin[1] + u * ex[1] + v * ey[1]
        return (float(x), float(y))

    return transform


def _check_within_bed(points: list[Point], printer: PrinterConfig) -> None:
    margin = 0.5  # mm de tolerancia
    for x, y in points:
        if not (-margin <= x <= printer.bed_width_mm + margin) or not (
            -margin <= y <= printer.bed_height_mm + margin
        ):
            raise GCodeGenerationError(
                f"Un punto generado ({x:.1f}, {y:.1f}) mm cae fuera de la mesa "
                f"({printer.bed_width_mm} x {printer.bed_height_mm} mm). "
                "Se aborta antes de generar G-code: revisa la deteccion de la "
                "plancha, el nesting, o el tamano configurado del cartel."
            )


@dataclass
class GCodeJob:
    gcode: str
    sign_count: int
    total_lines: int


def build_batch_gcode(
    placed_signs: list[tuple[SignArtwork, SignPlacement]],
    detection: SheetDetection,
    engrave: EngraveParams,
    printer: PrinterConfig,
) -> GCodeJob:
    if not placed_signs:
        raise GCodeGenerationError("No hay carteles para generar G-code.")

    transform = sheet_to_bed_transform(detection)
    travel_speed = min(printer.max_travel_speed_mm_min, 6000)
    s_value = round(engrave.power_percent / 100.0 * printer.max_laser_s_value)
    s_value = max(0, min(s_value, printer.max_laser_s_value))

    lines: list[str] = [
        "; Generado por falcon_batch (grabado en serie) - NO editar a mano",
        "G21 ; unidades: mm",
        "G90 ; coordenadas absolutas",
        "M5 ; laser apagado, por las dudas",
    ]

    for artwork, placement in placed_signs:
        lines.append(f"; ---- cartel: {artwork.text!r} (plantilla {artwork.template_name}) ----")

        if engrave.mode == "fill":
            segments = scanline_fill(artwork.polylines_mm, engrave.fill_interval_mm)
            paths_local: list[list[Point]] = [[a, b] for a, b in segments]
        else:
            paths_local = [list(c) + [c[0]] for c in artwork.polylines_mm if len(c) >= 2]

        for path_local in paths_local:
            bed_path = [
                transform(placement.x_in_sheet_mm + u, placement.y_in_sheet_mm + v)
                for (u, v) in path_local
            ]
            _check_within_bed(bed_path, printer)

            for _pass in range(max(1, engrave.passes)):
                x0, y0 = bed_path[0]
                lines.append(f"G0 X{x0:.3f} Y{y0:.3f} F{travel_speed:.0f}")
                lines.append(f"M4 S{s_value}")
                for x, y in bed_path[1:]:
                    lines.append(f"G1 X{x:.3f} Y{y:.3f} F{engrave.speed_mm_min:.0f}")
                lines.append("M5")

    lines.append(f"G0 X0 Y0 F{travel_speed:.0f}")
    lines.append("M5")
    lines.append("; fin del trabajo")

    gcode = "\n".join(lines) + "\n"
    return GCodeJob(gcode=gcode, sign_count=len(placed_signs), total_lines=len(lines))
