"""Calibracion de la camara de la Falcon2 Pro S: la camara de este modelo es
FIJA (no se mueve con el cabezal) y ve toda la mesa de una sola foto, con
bastante distorsion tipo "ojo de pez" en los bordes.

Se calibra marcando con clicks, en UNA sola foto, varios puntos cuya
posicion real en mm sobre la mesa conozcas (medidos con una regla, o
coincidiendo con referencias fisicas conocidas como las esquinas de la
bandeja). No hace falta mover la maquina para nada en este proceso.

Con esos puntos se arman DOS transformaciones, usadas juntas:

  - Interpolacion local (triangulacion de Delaunay + interpolacion
    baricentrica, via scipy): dentro del area cubierta por los puntos
    marcados, este metodo sigue la distorsion real de la lente punto a
    punto, en vez de asumir una unica transformacion pareja para toda la
    imagen. Es mucho mas preciso que una homografia global cuando la lente
    es gran angular, PERO solo funciona dentro del "casco convexo" de los
    puntos marcados (no puede extrapolar mas alla).
  - Homografia global: se usa como respaldo unicamente para pixeles que
    caen fuera de esa zona cubierta por los puntos (por eso conviene marcar
    puntos bien cerca de los bordes/esquinas reales de la bandeja: cuanto
    mas grande el area cubierta, menos casos caen en el respaldo, menos
    preciso).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from scipy.interpolate import LinearNDInterpolator


class CalibrationError(Exception):
    pass


@dataclass
class CameraCalibration:
    homography: np.ndarray  # 3x3, respaldo fuera del area cubierta por los puntos
    image_width: int
    image_height: int
    reprojection_error_mm: float
    points_px: np.ndarray  # (N,2), los puntos marcados (para la interpolacion local)
    points_mm: np.ndarray  # (N,2)
    _interp_x: object = field(default=None, init=False, repr=False, compare=False)
    _interp_y: object = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if len(self.points_px) >= 4:
            self._interp_x = LinearNDInterpolator(self.points_px, self.points_mm[:, 0])
            self._interp_y = LinearNDInterpolator(self.points_px, self.points_mm[:, 1])

    def _homography_fallback(self, points_px: np.ndarray) -> np.ndarray:
        pts = points_px.reshape(-1, 1, 2).astype(np.float64)
        out = cv2.perspectiveTransform(pts, self.homography)
        return out.reshape(-1, 2)

    def pixel_to_mm(self, px: float, py: float) -> tuple[float, float]:
        if self._interp_x is not None:
            mx = float(self._interp_x(px, py))
            my = float(self._interp_y(px, py))
            if not (np.isnan(mx) or np.isnan(my)):
                return mx, my
        out = self._homography_fallback(np.array([[px, py]], dtype=np.float64))
        return float(out[0, 0]), float(out[0, 1])

    def pixels_to_mm(self, points_px: np.ndarray) -> np.ndarray:
        """points_px: array (N,2). Devuelve array (N,2) en mm.

        Usa la interpolacion local donde es posible (dentro del area de los
        puntos de calibracion) y la homografia como respaldo para el resto."""
        points_px = np.asarray(points_px, dtype=np.float64)
        results = np.zeros((len(points_px), 2), dtype=np.float64)
        needs_fallback = np.ones(len(points_px), dtype=bool)

        if self._interp_x is not None:
            mx = self._interp_x(points_px[:, 0], points_px[:, 1])
            my = self._interp_y(points_px[:, 0], points_px[:, 1])
            ok = ~(np.isnan(mx) | np.isnan(my))
            results[ok, 0] = mx[ok]
            results[ok, 1] = my[ok]
            needs_fallback = ~ok

        if needs_fallback.any():
            fallback = self._homography_fallback(points_px[needs_fallback])
            results[needs_fallback] = fallback

        return results

    def to_dict(self) -> dict:
        return {
            "homography": self.homography.tolist(),
            "image_width": self.image_width,
            "image_height": self.image_height,
            "reprojection_error_mm": self.reprojection_error_mm,
            "points_px": self.points_px.tolist(),
            "points_mm": self.points_mm.tolist(),
        }

    @staticmethod
    def from_dict(d: dict) -> "CameraCalibration":
        return CameraCalibration(
            homography=np.array(d["homography"], dtype=np.float64),
            image_width=int(d["image_width"]),
            image_height=int(d["image_height"]),
            reprojection_error_mm=float(d["reprojection_error_mm"]),
            points_px=np.array(d.get("points_px", []), dtype=np.float64),
            points_mm=np.array(d.get("points_mm", []), dtype=np.float64),
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
        points_px=src,
        points_mm=dst,
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
