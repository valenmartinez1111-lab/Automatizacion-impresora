"""Calibracion de la camara de la Falcon2 Pro S: la camara de este modelo es
FIJA (no se mueve con el cabezal) y ve toda la mesa de una sola foto, con
bastante distorsion tipo "ojo de pez" en los bordes.

Se calibra marcando con clicks, en UNA sola foto, varios puntos cuya
posicion real en mm sobre la mesa conozcas (medidos con una regla, o
coincidiendo con referencias fisicas conocidas como las esquinas de la
bandeja). Con esos puntos se ajusta una homografia (transformacion
proyectiva) de pixeles a mm.

Una homografia no corrige perfectamente una distorsion de lente tipo ojo de
pez (eso requeriria un modelo de distorsion radial, tipico de una
calibracion de camara con tablero de ajedrez). Por eso conviene marcar
BASTANTES puntos (6 o mas), bien distribuidos por toda la imagen -- cuantos
mas puntos y mas repartidos, mejor absorbe el ajuste la distorsion real de
la lente. No hace falta mover la maquina para nada en este proceso.
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
        raise CalibrationError("Se necesitan al menos 4 puntos para calibrar (6 o mas, mejor).")

    src = np.array(points_px, dtype=np.float64)
    dst = np.array(points_mm, dtype=np.float64)

    # con 4 puntos exactos: solucion exacta (method=0). Con mas de 4 (recomendado,
    # para que absorba mejor la distorsion de la lente): minimos cuadrados.
    method = 0 if len(points_px) == 4 else cv2.RANSAC
    H, _mask = cv2.findHomography(src, dst, method=method, ransacReprojThreshold=5.0)
    if H is None:
        raise CalibrationError("No se pudo calcular la homografia con los puntos dados.")

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
