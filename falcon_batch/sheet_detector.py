"""Deteccion de la plancha de material sobre la mesa, por diferencia de fondo.

No usa marcadores. La idea es simple y robusta a cualquier color de material:

  1. Antes de apoyar el material, se saca una foto de referencia de la mesa vacia.
  2. Con el material puesto, se saca otra foto.
  3. Se comparan: lo que cambio es, casi con certeza, la plancha (mas alguna sombra
     o reflejo, que se filtra con umbral + limpieza morfologica).
  4. Se ajusta el rectangulo minimo que contiene esa diferencia (minAreaRect), se
     valida que sea razonablemente rectangular, y se convierten sus 4 esquinas de
     pixeles a mm usando la homografia calculada en la calibracion.

Esto da la posicion, tamano y rotacion reales de la plancha en la mesa, sin
importar donde ni con que angulo la hayas apoyado.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .calibration import CameraCalibration

MIN_AREA_PX_RATIO = 0.015  # una plancha valida ocupa al menos ~1.5% del cuadro
MIN_RECTANGULARITY = 0.82  # contourArea / minAreaRect area; 1.0 = rectangulo perfecto
MIN_SHEET_DIM_MM = 15.0  # por debajo de esto, seguramente es ruido, no una plancha


class SheetDetectionError(Exception):
    pass


@dataclass
class SheetDetection:
    corners_px: np.ndarray  # (4,2) orden TL,TR,BR,BL en pixeles
    corners_mm: np.ndarray  # (4,2) mismas esquinas, en mm sobre la mesa
    center_mm: tuple[float, float]
    width_mm: float
    height_mm: float
    rotation_deg: float
    rectangularity: float
    changed_area_ratio: float
    debug_image_path: str | None = None


def _order_points(pts: np.ndarray) -> np.ndarray:
    """Ordena 4 puntos como TL, TR, BR, BL (algoritmo estandar suma/diferencia)."""
    rect = np.zeros((4, 2), dtype=np.float64)
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]  # top-left: menor x+y
    rect[2] = pts[np.argmax(s)]  # bottom-right: mayor x+y
    diff = np.diff(pts, axis=1).reshape(-1)
    rect[1] = pts[np.argmin(diff)]  # top-right: menor y-x
    rect[3] = pts[np.argmax(diff)]  # bottom-left: mayor y-x
    return rect


def _largest_contour_mask(reference_bgr: np.ndarray, current_bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if reference_bgr.shape != current_bgr.shape:
        current_bgr = cv2.resize(current_bgr, (reference_bgr.shape[1], reference_bgr.shape[0]))

    ref_gray = cv2.cvtColor(reference_bgr, cv2.COLOR_BGR2GRAY)
    cur_gray = cv2.cvtColor(current_bgr, cv2.COLOR_BGR2GRAY)
    ref_gray = cv2.GaussianBlur(ref_gray, (5, 5), 0)
    cur_gray = cv2.GaussianBlur(cur_gray, (5, 5), 0)

    diff = cv2.absdiff(ref_gray, cur_gray)
    _, mask = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=3)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return mask, contours


def detect_sheet(
    reference_bgr: np.ndarray,
    current_bgr: np.ndarray,
    calibration: CameraCalibration,
    bed_width_mm: float,
    bed_height_mm: float,
    debug_output_path: str | None = None,
) -> SheetDetection:
    h, w = current_bgr.shape[:2]
    image_area = float(h * w)

    mask, contours = _largest_contour_mask(reference_bgr, current_bgr)
    if not contours:
        raise SheetDetectionError(
            "No se detecto ningun cambio entre la foto de referencia y la actual. "
            "¿Se coloco el material? ¿La foto de referencia es de la mesa realmente vacia?"
        )

    largest = max(contours, key=cv2.contourArea)
    contour_area = cv2.contourArea(largest)
    changed_area_ratio = contour_area / image_area

    if changed_area_ratio < MIN_AREA_PX_RATIO:
        raise SheetDetectionError(
            f"El area detectada es demasiado chica ({changed_area_ratio * 100:.2f}% del cuadro). "
            "Puede ser ruido/sombra en vez de una plancha real."
        )

    rect = cv2.minAreaRect(largest)  # ((cx,cy),(w,h),angle)
    (rect_w_px, rect_h_px) = rect[1]
    rect_area_px = rect_w_px * rect_h_px
    rectangularity = contour_area / rect_area_px if rect_area_px > 0 else 0.0

    if rectangularity < MIN_RECTANGULARITY:
        raise SheetDetectionError(
            f"La forma detectada no parece un rectangulo limpio (rectangularidad "
            f"{rectangularity:.2f}, minimo {MIN_RECTANGULARITY}). Puede haber algo mas "
            "sobre la mesa ademas del material, o el material no es plano/rectangular."
        )

    box_px = cv2.boxPoints(rect)
    box_px = _order_points(box_px)

    corners_mm = calibration.pixels_to_mm(box_px)

    def dist(a, b):
        return float(np.linalg.norm(np.array(a) - np.array(b)))

    width_mm = (dist(corners_mm[0], corners_mm[1]) + dist(corners_mm[3], corners_mm[2])) / 2.0
    height_mm = (dist(corners_mm[0], corners_mm[3]) + dist(corners_mm[1], corners_mm[2])) / 2.0
    center_mm = tuple(corners_mm.mean(axis=0))

    if width_mm < MIN_SHEET_DIM_MM or height_mm < MIN_SHEET_DIM_MM:
        raise SheetDetectionError(
            f"La plancha detectada es demasiado chica ({width_mm:.1f} x {height_mm:.1f} mm)."
        )
    if width_mm > bed_width_mm + 5 or height_mm > bed_height_mm + 5:
        raise SheetDetectionError(
            f"La plancha detectada ({width_mm:.1f} x {height_mm:.1f} mm) es mas grande que "
            f"la mesa configurada ({bed_width_mm} x {bed_height_mm} mm). Revisa la calibracion "
            "de camara: puede estar mal calculada la homografia."
        )

    for cx, cy in corners_mm:
        margin = 2.0
        if not (-margin <= cx <= bed_width_mm + margin) or not (-margin <= cy <= bed_height_mm + margin):
            raise SheetDetectionError(
                f"Una esquina detectada ({cx:.1f}, {cy:.1f}) cae fuera del area de la mesa "
                f"({bed_width_mm} x {bed_height_mm} mm). No se va a procesar por seguridad."
            )

    edge_vec = corners_mm[1] - corners_mm[0]
    rotation_deg = float(np.degrees(np.arctan2(edge_vec[1], edge_vec[0])))

    debug_path = None
    if debug_output_path:
        overlay = current_bgr.copy()
        cv2.drawContours(overlay, [largest], -1, (0, 255, 0), 2)
        box_int = box_px.astype(int)
        cv2.polylines(overlay, [box_int], True, (0, 0, 255), 3)
        for i, (px, py) in enumerate(box_px):
            cv2.circle(overlay, (int(px), int(py)), 8, (0, 0, 255), -1)
            mx, my = corners_mm[i]
            cv2.putText(
                overlay,
                f"({mx:.0f},{my:.0f})mm",
                (int(px) + 10, int(py)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 0),
                2,
            )
        cv2.imwrite(debug_output_path, overlay)
        debug_path = debug_output_path

    return SheetDetection(
        corners_px=box_px,
        corners_mm=corners_mm,
        center_mm=center_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        rotation_deg=rotation_deg,
        rectangularity=rectangularity,
        changed_area_ratio=changed_area_ratio,
        debug_image_path=debug_path,
    )
