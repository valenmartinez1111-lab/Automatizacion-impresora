"""Piezas compartidas para detectar el rectangulo de la plancha de material:
el tipo de resultado (SheetDetection), el ajuste de rectangulo a partir de
una nube de puntos en mm, y la deteccion de pixeles "cambiados" entre dos
fotos (referencia vs. actual) por diferencia de fondo.

Estas piezas las usa grid_scan.py, que es quien arma la nube de puntos
completa recorriendo la mesa en varias posiciones (la camara de la Falcon2
esta montada en el cabezal y no ve toda la mesa de una sola foto, asi que
ya no existe una deteccion "de una sola imagen global").
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

MIN_RECTANGULARITY = 0.80  # area del hull / area del rectangulo minimo; 1.0 = rectangulo perfecto
MIN_SHEET_DIM_MM = 15.0  # por debajo de esto, seguramente es ruido, no una plancha
MIN_POINTS_FOR_FIT = 30  # puntos "cambiados" minimos para confiar en el ajuste


class SheetDetectionError(Exception):
    pass


@dataclass
class SheetDetection:
    corners_mm: np.ndarray  # (4,2) esquinas del rectangulo detectado, en mm sobre la mesa
    center_mm: tuple[float, float]
    width_mm: float
    height_mm: float
    rotation_deg: float
    rectangularity: float
    changed_area_ratio: float
    debug_image_path: str | None = None
    debug_image_paths: list[str] | None = None  # varias fotos, para el escaneo en grilla


def _order_points(pts: np.ndarray) -> np.ndarray:
    """Ordena 4 puntos como TL, TR, BR, BL (algoritmo estandar suma/diferencia).
    Funciona igual en pixeles o en mm: solo depende de la geometria relativa."""
    rect = np.zeros((4, 2), dtype=np.float64)
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1).reshape(-1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def compute_changed_mask(reference_bgr: np.ndarray, current_bgr: np.ndarray) -> np.ndarray:
    """Mascara binaria (255 = cambio) entre dos fotos del mismo encuadre,
    por diferencia de fondo + umbral de Otsu + limpieza morfologica.
    Se usa por-celda dentro de grid_scan.py."""
    if reference_bgr.shape != current_bgr.shape:
        current_bgr = cv2.resize(current_bgr, (reference_bgr.shape[1], reference_bgr.shape[0]))

    ref_gray = cv2.cvtColor(reference_bgr, cv2.COLOR_BGR2GRAY)
    cur_gray = cv2.cvtColor(current_bgr, cv2.COLOR_BGR2GRAY)
    ref_gray = cv2.GaussianBlur(ref_gray, (5, 5), 0)
    cur_gray = cv2.GaussianBlur(cur_gray, (5, 5), 0)

    diff = cv2.absdiff(ref_gray, cur_gray)
    _, mask = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    return mask


def fit_sheet_from_points_mm(
    points_mm: np.ndarray,
    bed_width_mm: float,
    bed_height_mm: float,
    scanned_area_mm2: float,
) -> SheetDetection:
    """Ajusta el rectangulo de la plancha a partir de una nube de puntos en mm
    (todos los pixeles marcados como 'cambiaron' en algun parche del escaneo,
    ya convertidos a coordenadas de la mesa). Valida que el resultado sea
    razonable antes de devolverlo."""
    if len(points_mm) < MIN_POINTS_FOR_FIT:
        raise SheetDetectionError(
            f"Muy pocos puntos de cambio detectados ({len(points_mm)}, minimo "
            f"{MIN_POINTS_FOR_FIT}). ¿Se coloco el material? ¿Las fotos de referencia "
            "son realmente de la mesa vacia, con la misma luz?"
        )

    pts32 = points_mm.astype(np.float32)
    hull = cv2.convexHull(pts32)
    hull_area = cv2.contourArea(hull)

    rect = cv2.minAreaRect(pts32)  # ((cx,cy),(w,h),angle)
    rect_w_mm, rect_h_mm = rect[1]
    rect_area_mm2 = rect_w_mm * rect_h_mm
    rectangularity = hull_area / rect_area_mm2 if rect_area_mm2 > 0 else 0.0

    if rectangularity < MIN_RECTANGULARITY:
        raise SheetDetectionError(
            f"La forma detectada no parece un rectangulo limpio (rectangularidad "
            f"{rectangularity:.2f}, minimo {MIN_RECTANGULARITY}). Puede haber algo mas "
            "sobre la mesa ademas del material, o el material no es plano/rectangular."
        )

    box_mm = cv2.boxPoints(rect).astype(np.float64)
    box_mm = _order_points(box_mm)

    def dist(a, b):
        return float(np.linalg.norm(np.array(a) - np.array(b)))

    width_mm = (dist(box_mm[0], box_mm[1]) + dist(box_mm[3], box_mm[2])) / 2.0
    height_mm = (dist(box_mm[0], box_mm[3]) + dist(box_mm[1], box_mm[2])) / 2.0
    center_mm = tuple(box_mm.mean(axis=0))

    if width_mm < MIN_SHEET_DIM_MM or height_mm < MIN_SHEET_DIM_MM:
        raise SheetDetectionError(
            f"La plancha detectada es demasiado chica ({width_mm:.1f} x {height_mm:.1f} mm)."
        )
    if width_mm > bed_width_mm + 5 or height_mm > bed_height_mm + 5:
        raise SheetDetectionError(
            f"La plancha detectada ({width_mm:.1f} x {height_mm:.1f} mm) es mas grande que "
            f"la mesa configurada ({bed_width_mm} x {bed_height_mm} mm). Revisa la calibracion "
            "de camara."
        )

    for cx, cy in box_mm:
        margin = 2.0
        if not (-margin <= cx <= bed_width_mm + margin) or not (-margin <= cy <= bed_height_mm + margin):
            raise SheetDetectionError(
                f"Una esquina detectada ({cx:.1f}, {cy:.1f}) cae fuera del area de la mesa "
                f"({bed_width_mm} x {bed_height_mm} mm). No se va a procesar por seguridad."
            )

    edge_vec = box_mm[1] - box_mm[0]
    rotation_deg = float(np.degrees(np.arctan2(edge_vec[1], edge_vec[0])))

    changed_area_ratio = rect_area_mm2 / scanned_area_mm2 if scanned_area_mm2 > 0 else 0.0

    return SheetDetection(
        corners_mm=box_mm,
        center_mm=center_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        rotation_deg=rotation_deg,
        rectangularity=rectangularity,
        changed_area_ratio=changed_area_ratio,
    )
