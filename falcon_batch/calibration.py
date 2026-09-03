"""Calibracion camara -> mm de la mesa de la Falcon2 Pro S.

No usa marcadores pegados a la plancha (eso se decidio evitar). En cambio,
es una calibracion UNICA por instalacion de camara: le mostras al programa
4 o mas puntos cuya posicion en mm sobre la mesa ya conoces (por ejemplo,
las 4 esquinas de la bandeja/panal, medidas con una regla, o las esquinas
de una pieza de prueba rectangular apoyada en una posicion conocida), haces
click sobre ellos en la imagen, y se calcula una homografia que despues usa
sheet_detector.py para convertir cualquier contorno detectado de pixeles a mm.

Se corre una sola vez (o cada vez que se mueva/reemplace la camara).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


class CalibrationError(Exception):
    pass


@dataclass
class CameraCalibration:
    homography: np.ndarray  # 3x3, mapea (px, py, 1) -> (mm_x, mm_y, w)
    image_width: int
    image_height: int
    reprojection_error_mm: float

    def pixel_to_mm(self, px: float, py: float) -> tuple[float, float]:
        vec = np.array([px, py, 1.0], dtype=np.float64)
        out = self.homography @ vec
        out /= out[2]
        return float(out[0]), float(out[1])

    def pixels_to_mm(self, points_px: np.ndarray) -> np.ndarray:
        """points_px: array (N,2). Devuelve array (N,2) en mm."""
        pts = points_px.reshape(-1, 1, 2).astype(np.float64)
        out = cv2.perspectiveTransform(pts, self.homography)
        return out.reshape(-1, 2)

    def to_dict(self) -> dict:
        return {
            "homography": self.homography.tolist(),
            "image_width": self.image_width,
            "image_height": self.image_height,
            "reprojection_error_mm": self.reprojection_error_mm,
        }

    @staticmethod
    def from_dict(d: dict) -> "CameraCalibration":
        return CameraCalibration(
            homography=np.array(d["homography"], dtype=np.float64),
            image_width=int(d["image_width"]),
            image_height=int(d["image_height"]),
            reprojection_error_mm=float(d["reprojection_error_mm"]),
        )


def compute_homography(
    points_px: list[tuple[float, float]],
    points_mm: list[tuple[float, float]],
    image_width: int,
    image_height: int,
) -> CameraCalibration:
    if len(points_px) != len(points_mm):
        raise CalibrationError("La cantidad de puntos en pixeles y en mm no coincide.")
    if len(points_px) < 4:
        raise CalibrationError("Se necesitan al menos 4 puntos para calibrar.")

    src = np.array(points_px, dtype=np.float64)
    dst = np.array(points_mm, dtype=np.float64)

    H, mask = cv2.findHomography(src, dst, method=0)
    if H is None:
        raise CalibrationError("No se pudo calcular la homografia con los puntos dados.")

    # error de reproyeccion, en mm, para que el usuario sepa que tan buena quedo
    # la calibracion (deberia ser << 1mm; si da varios mm, repetir con mas cuidado).
    projected = cv2.perspectiveTransform(src.reshape(-1, 1, 2), H).reshape(-1, 2)
    errors = np.linalg.norm(projected - dst, axis=1)
    reprojection_error_mm = float(np.mean(errors))

    return CameraCalibration(
        homography=H,
        image_width=image_width,
        image_height=image_height,
        reprojection_error_mm=reprojection_error_mm,
    )


def save_calibration(cal: CameraCalibration, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(cal.to_dict(), f, indent=2)


def load_calibration(path: str) -> CameraCalibration:
    p = Path(path)
    if not p.exists():
        raise CalibrationError(
            f"No existe el archivo de calibracion '{path}'. "
            "Corre 'python calibrate.py' antes de usar el detector de plancha."
        )
    with p.open("r", encoding="utf-8") as f:
        d = json.load(f)
    return CameraCalibration.from_dict(d)
