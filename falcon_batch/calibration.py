"""Calibracion LOCAL de la camara de la Falcon2 Pro S.

La camara de esta maquina esta montada sobre el cabezal: se mueve con el
laser y solo ve una zona chica de la mesa por vez (no toda junta). Por eso
NO se calibra con una homografia global (4 esquinas de la mesa): lo que hace
falta es saber, para una foto tomada con el cabezal en una posicion dada,
cuantos mm reales representa cada pixel alrededor del centro de esa foto (y
si la camara esta rotada/espejada respecto de los ejes X/Y de la maquina).

Se calibra sin reglas ni patrones impresos: se mueve el cabezal una
distancia CONOCIDA en X y otra en Y, y la persona marca (con un click) el
mismo punto fisico visible antes y despues de cada movimiento. El propio
movimiento medido de la maquina sirve de referencia.

Matematica (resumen):
  Si la camara se mueve una distancia real dx (eje X de la maquina) y un
  punto fijo de la mesa aparecia en el pixel p0 antes de moverse, va a
  aparecer en el pixel p1 = p0 - T(dx, 0) despues [T: mm -> pixeles, lineal],
  porque al moverse la camara hacia +X, todo lo que ve se corre en sentido
  opuesto dentro de la imagen. De (p1 - p0) y (dx, dy) conocidos se despeja
  la matriz T, y de ahi su inversa (pixeles -> mm), que es lo que se guarda.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


class CalibrationError(Exception):
    pass


@dataclass
class LocalCalibration:
    """mm_to_px: matriz 2x2 tal que pixel_delta = mm_to_px @ mm_delta
    (como se mueve la CAMARA, no el punto). Se guarda tambien su inversa
    para no recalcularla en cada uso."""

    mm_to_px: np.ndarray
    px_to_mm: np.ndarray
    image_width: int
    image_height: int

    def point_to_mm_offset(self, px: float, py: float) -> tuple[float, float]:
        """Dado un pixel de una foto (tomada con el cabezal en una posicion
        conocida), devuelve el offset en mm de ese punto respecto de la
        posicion del cabezal en el momento de la foto."""
        cx, cy = self.image_width / 2.0, self.image_height / 2.0
        pixel_offset = np.array([px - cx, py - cy], dtype=np.float64)
        # ver derivacion en el docstring del modulo: el offset real es
        # -(px_to_mm @ pixel_offset), no +.
        mm_offset = -(self.px_to_mm @ pixel_offset)
        return float(mm_offset[0]), float(mm_offset[1])

    def points_to_mm_offsets(self, points_px: np.ndarray) -> np.ndarray:
        """points_px: array (N,2) de pixeles. Devuelve array (N,2) de offsets
        en mm respecto de la posicion del cabezal (vectorizado)."""
        cx, cy = self.image_width / 2.0, self.image_height / 2.0
        offsets_px = points_px.astype(np.float64) - np.array([cx, cy])
        offsets_mm = -(offsets_px @ self.px_to_mm.T)
        return offsets_mm

    def fov_extent_mm(self) -> tuple[float, float]:
        """Estimacion del ancho/alto en mm que cubre el cuadro completo de la
        camara, usando las esquinas de la imagen. Se usa para planificar la
        grilla de escaneo (grid_scan.py), no necesita ser exacta."""
        w, h = self.image_width, self.image_height
        corners_px = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float64)
        offsets_mm = self.points_to_mm_offsets(corners_px)
        width_mm = float(offsets_mm[:, 0].max() - offsets_mm[:, 0].min())
        height_mm = float(offsets_mm[:, 1].max() - offsets_mm[:, 1].min())
        return width_mm, height_mm

    def to_dict(self) -> dict:
        return {
            "mm_to_px": self.mm_to_px.tolist(),
            "px_to_mm": self.px_to_mm.tolist(),
            "image_width": self.image_width,
            "image_height": self.image_height,
        }

    @staticmethod
    def from_dict(d: dict) -> "LocalCalibration":
        return LocalCalibration(
            mm_to_px=np.array(d["mm_to_px"], dtype=np.float64),
            px_to_mm=np.array(d["px_to_mm"], dtype=np.float64),
            image_width=int(d["image_width"]),
            image_height=int(d["image_height"]),
        )


def compute_local_calibration(
    point_before_x: tuple[float, float],
    point_after_x: tuple[float, float],
    dx_mm: float,
    point_before_y: tuple[float, float],
    point_after_y: tuple[float, float],
    dy_mm: float,
    image_width: int,
    image_height: int,
) -> LocalCalibration:
    """Calcula la calibracion local a partir de dos pares de clicks:
    - point_before_x / point_after_x: el mismo punto fisico, marcado antes y
      despues de mover el cabezal dx_mm en X (con Y sin moverse).
    - point_before_y / point_after_y: idem, moviendo dy_mm en Y.

    dx_mm y dy_mm son las distancias que se le pidio a la maquina que se
    mueva (con signo: positivo = sentido +X o +Y de la maquina).
    """
    if abs(dx_mm) < 1e-6 or abs(dy_mm) < 1e-6:
        raise CalibrationError("dx_mm y dy_mm deben ser distintos de cero.")

    delta_px_x = np.array(point_after_x, dtype=np.float64) - np.array(
        point_before_x, dtype=np.float64
    )
    delta_px_y = np.array(point_after_y, dtype=np.float64) - np.array(
        point_before_y, dtype=np.float64
    )

    # mm_to_px @ [dx,0] = delta_px_x  =>  columna 0 = delta_px_x / dx_mm
    # mm_to_px @ [0,dy] = delta_px_y  =>  columna 1 = delta_px_y / dy_mm
    mm_to_px = np.column_stack([delta_px_x / dx_mm, delta_px_y / dy_mm])

    try:
        px_to_mm = np.linalg.inv(mm_to_px)
    except np.linalg.LinAlgError as exc:
        raise CalibrationError(
            "No se pudo invertir la matriz de calibracion: los dos movimientos "
            "(X e Y) dieron desplazamientos de pixeles casi paralelos. Repeti "
            "la calibracion asegurandote de mover en X y en Y por separado."
        ) from exc

    return LocalCalibration(
        mm_to_px=mm_to_px,
        px_to_mm=px_to_mm,
        image_width=image_width,
        image_height=image_height,
    )


def save_calibration(cal: LocalCalibration, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(cal.to_dict(), f, indent=2)


def load_calibration(path: str) -> LocalCalibration:
    p = Path(path)
    if not p.exists():
        raise CalibrationError(
            f"No existe el archivo de calibracion '{path}'. "
            "Corre 'python calibrate.py' antes de usar el detector de plancha."
        )
    with p.open("r", encoding="utf-8") as f:
        d = json.load(f)
    return LocalCalibration.from_dict(d)
