"""Captura de imagenes desde la camara USB de la Falcon2 Pro S (o una webcam externa)."""
from __future__ import annotations

import datetime
import time
from pathlib import Path

import cv2
import numpy as np

from .config import CameraConfig


class CameraError(Exception):
    pass


class Camera:
    """Wrapper fino sobre cv2.VideoCapture con reintentos y guardado de snapshots.

    IMPORTANTE: Falcon Design Space debe estar cerrado mientras se usa esta clase.
    La camara UVC no admite dos procesos leyendola al mismo tiempo; si FDS la tiene
    abierta, esto va a fallar al abrir o va a devolver frames vacios/congelados.
    """

    def __init__(self, cfg: CameraConfig, snapshot_dir: str = "logs/photos"):
        self.cfg = cfg
        self.snapshot_dir = Path(snapshot_dir)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._cap: cv2.VideoCapture | None = None

    def open(self) -> None:
        cap = cv2.VideoCapture(self.cfg.device_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            # reintento sin backend explicito, por si CAP_DSHOW no esta disponible
            cap = cv2.VideoCapture(self.cfg.device_index)
        if not cap.isOpened():
            raise CameraError(
                f"No se pudo abrir la camara indice {self.cfg.device_index}. "
                "Verifica que Falcon Design Space este cerrado y que el indice sea correcto "
                "(probar con calibration/list_cameras.py)."
            )
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.capture_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.capture_height)
        self._cap = cap
        # descartar los primeros frames: muchas webcams tardan en estabilizar
        # exposicion/balance de blancos.
        for _ in range(5):
            self._cap.read()
            time.sleep(0.05)

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def __enter__(self) -> "Camera":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def capture(self, label: str = "frame", save: bool = True) -> tuple[np.ndarray, str | None]:
        """Captura un frame. Devuelve (imagen_bgr, ruta_guardada_o_None)."""
        if self._cap is None:
            raise CameraError("La camara no esta abierta. Usa Camera(...) con 'with' u open().")

        # descartar un frame extra: el buffer de la camara suele tener 1-2 frames
        # viejos en cola, y queremos la imagen mas reciente posible.
        self._cap.read()
        ok, frame = self._cap.read()
        if not ok or frame is None:
            raise CameraError("La camara no devolvio una imagen valida.")

        path = None
        if save:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = str(self.snapshot_dir / f"{ts}_{label}.jpg")
            cv2.imwrite(path, frame)
        return frame, path
