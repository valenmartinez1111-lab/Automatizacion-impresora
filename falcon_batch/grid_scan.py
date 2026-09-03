"""Deteccion de la plancha por barrido de la mesa.

La camara de la Falcon2 Pro S esta montada en el cabezal: se mueve con el
laser y solo ve una zona chica por vez, no la mesa completa. Para "ver" toda
la plancha, el agente mueve el cabezal (laser apagado) por una grilla de
posiciones que cubre la mesa con solape, y en cada parada saca una foto.

Se hace esto dos veces: una con la mesa vacia (referencia) y otra con el
material puesto. Comparando parche por parche (misma tecnica de diferencia
de fondo que antes, ver sheet_detector.compute_changed_mask), se arma una
nube de puntos en mm de "todo lo que cambio" en toda la mesa, y sobre esa
nube se ajusta el rectangulo de la plancha (sheet_detector.fit_sheet_from_points_mm).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .calibration import LocalCalibration
from .camera import Camera
from .grbl_sender import GrblSender
from .sheet_detector import SheetDetection, SheetDetectionError, compute_changed_mask, fit_sheet_from_points_mm

DOWNSAMPLE_STEP = 6  # 1 de cada N pixeles cambiados, para no acumular nubes de puntos gigantes
SETTLE_S = 0.35  # pausa despues de cada movimiento, antes de sacar la foto (vibracion mecanica)


@dataclass
class ScanCell:
    head_x_mm: float
    head_y_mm: float
    frame: np.ndarray
    photo_path: str | None


def plan_scan_grid(
    bed_width_mm: float,
    bed_height_mm: float,
    fov_width_mm: float,
    fov_height_mm: float,
    margin_mm: float = 15.0,
    overlap_fraction: float = 0.45,
) -> list[tuple[float, float]]:
    """Posiciones (x,y) del cabezal que cubren la mesa, con solape suficiente
    para no perder el borde de la plancha entre celdas contiguas."""
    if not (0.0 <= overlap_fraction < 0.9):
        raise ValueError("overlap_fraction debe estar entre 0 y 0.9")

    step_x = max(fov_width_mm * (1.0 - overlap_fraction), 10.0)
    step_y = max(fov_height_mm * (1.0 - overlap_fraction), 10.0)

    x_max = bed_width_mm - margin_mm
    y_max = bed_height_mm - margin_mm
    if margin_mm >= x_max or margin_mm >= y_max:
        raise ValueError("El margen configurado no deja area util para escanear.")

    xs = list(np.arange(margin_mm, x_max, step_x))
    if not xs or xs[-1] < x_max - 1e-6:
        xs.append(x_max)
    ys = list(np.arange(margin_mm, y_max, step_y))
    if not ys or ys[-1] < y_max - 1e-6:
        ys.append(y_max)

    return [(float(x), float(y)) for y in ys for x in xs]


class GridScanner:
    def __init__(
        self,
        camera: Camera,
        grbl: GrblSender,
        calibration: LocalCalibration,
        bed_width_mm: float,
        bed_height_mm: float,
        travel_feed_mm_min: float = 3000.0,
        margin_mm: float = 15.0,
        overlap_fraction: float = 0.45,
    ):
        self.camera = camera
        self.grbl = grbl
        self.calibration = calibration
        self.bed_width_mm = bed_width_mm
        self.bed_height_mm = bed_height_mm
        self.travel_feed_mm_min = travel_feed_mm_min

        self.fov_width_mm, self.fov_height_mm = calibration.fov_extent_mm()
        self.grid_positions = plan_scan_grid(
            bed_width_mm, bed_height_mm, self.fov_width_mm, self.fov_height_mm, margin_mm, overlap_fraction
        )

    def scan(self, label: str) -> list[ScanCell]:
        """Recorre toda la grilla y saca una foto en cada parada. El laser
        nunca se prende durante este recorrido: move_to_absolute manda M5
        (laser apagado) antes de cada movimiento, por las dudas."""
        cells: list[ScanCell] = []
        for i, (x, y) in enumerate(self.grid_positions):
            self.grbl.move_to_absolute(x, y, self.travel_feed_mm_min)
            time.sleep(SETTLE_S)
            frame, path = self.camera.capture(label=f"{label}_{i:03d}_x{x:.0f}_y{y:.0f}")
            cells.append(ScanCell(head_x_mm=x, head_y_mm=y, frame=frame, photo_path=path))
        return cells


def detect_sheet_from_scans(
    reference_cells: list[ScanCell],
    current_cells: list[ScanCell],
    calibration: LocalCalibration,
    bed_width_mm: float,
    bed_height_mm: float,
    debug_dir: str | None = None,
    max_debug_images: int = 12,
) -> SheetDetection:
    if len(reference_cells) != len(current_cells):
        raise SheetDetectionError(
            "El escaneo de referencia y el actual tienen distinta cantidad de "
            "posiciones; algo cambio la grilla entre medio."
        )

    if debug_dir:
        Path(debug_dir).mkdir(parents=True, exist_ok=True)

    fov_w, fov_h = calibration.fov_extent_mm()
    all_points_mm: list[np.ndarray] = []
    debug_paths: list[str] = []
    scanned_area_mm2 = 0.0

    for ref_cell, cur_cell in zip(reference_cells, current_cells):
        if (ref_cell.head_x_mm, ref_cell.head_y_mm) != (cur_cell.head_x_mm, cur_cell.head_y_mm):
            raise SheetDetectionError(
                "Las posiciones del escaneo de referencia y el actual no coinciden."
            )
        scanned_area_mm2 += fov_w * fov_h

        mask = compute_changed_mask(ref_cell.frame, cur_cell.frame)
        ys, xs = np.nonzero(mask)
        if len(xs) == 0:
            continue

        xs_ds = xs[::DOWNSAMPLE_STEP]
        ys_ds = ys[::DOWNSAMPLE_STEP]
        pixel_points = np.column_stack([xs_ds, ys_ds])
        offsets_mm = calibration.points_to_mm_offsets(pixel_points)
        points_mm = offsets_mm + np.array([cur_cell.head_x_mm, cur_cell.head_y_mm])
        all_points_mm.append(points_mm)

        if debug_dir and len(debug_paths) < max_debug_images:
            overlay = cur_cell.frame.copy()
            overlay[mask > 0] = (0, 0, 255)
            blended = cv2.addWeighted(cur_cell.frame, 0.6, overlay, 0.4, 0)
            path = f"{debug_dir}/celda_x{cur_cell.head_x_mm:.0f}_y{cur_cell.head_y_mm:.0f}.jpg"
            cv2.imwrite(path, blended)
            debug_paths.append(path)

    if not all_points_mm:
        raise SheetDetectionError(
            "No se detecto ningun cambio en ninguna posicion del escaneo. "
            "¿Se coloco el material? ¿La referencia es realmente de la mesa vacia?"
        )

    points_mm = np.concatenate(all_points_mm, axis=0)
    detection = fit_sheet_from_points_mm(points_mm, bed_width_mm, bed_height_mm, scanned_area_mm2)
    detection.debug_image_paths = debug_paths
    return detection
