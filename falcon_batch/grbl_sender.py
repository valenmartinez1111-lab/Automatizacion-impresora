"""Envio de G-code a la Falcon2 Pro S por puerto serie, hablando el protocolo
GRBL directamente (sin pasar por Falcon Design Space).

Protocolo de streaming linea por linea: se manda una linea, se espera "ok" (o
"error:N") antes de mandar la siguiente. Es el metodo mas simple y seguro de
streaming GRBL (mas lento que streaming por caracteres contando buffer, pero
para trabajos de grabado de texto -no de video en tiempo real- la diferencia
de velocidad no importa, y es mucho mas facil de razonar y de frenar a tiempo).

IMPORTANTE: Falcon Design Space tiene que estar CERRADO. El puerto serie no
se puede compartir entre dos programas.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

import serial

STATUS_RE = re.compile(r"<(\w+)[,|]")
FULL_STATUS_RE = re.compile(
    r"<(\w+)[,|].*?MPos:(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*)"
)

WAKE_TIMEOUT_S = 5.0
LINE_TIMEOUT_S = 30.0
IDLE_POLL_INTERVAL_S = 0.3


class GrblError(Exception):
    pass


class GrblAlarm(GrblError):
    """La maquina entro en estado ALARM. No se reintenta automaticamente:
    requiere que un humano revise la maquina antes de desbloquear ($X)."""


@dataclass
class SendProgress:
    line_number: int
    total_lines: int
    line_text: str


class GrblSender:
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 2.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._ser: serial.Serial | None = None

    def connect(self) -> None:
        self._ser = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
        time.sleep(2.0)  # muchas placas GRBL resetean al abrir el puerto serie
        self._ser.reset_input_buffer()
        self._ser.write(b"\r\n\r\n")
        time.sleep(2.0)
        self._ser.reset_input_buffer()

    def disconnect(self) -> None:
        if self._ser is not None:
            self._ser.close()
            self._ser = None

    def __enter__(self) -> "GrblSender":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.disconnect()

    def _require_connection(self) -> serial.Serial:
        if self._ser is None:
            raise GrblError("No hay conexion serie abierta. Llama a connect() primero.")
        return self._ser

    def get_full_status(self) -> dict:
        """Consulta el estado actual via '?': estado (Idle/Run/Alarm/...) y,
        si la respuesta la incluye, la posicion absoluta de la maquina (MPos)."""
        ser = self._require_connection()
        ser.reset_input_buffer()
        ser.write(b"?")
        deadline = time.time() + 3.0
        while time.time() < deadline:
            line = ser.readline().decode(errors="replace").strip()
            if not line:
                continue
            m = FULL_STATUS_RE.search(line)
            if m:
                return {
                    "state": m.group(1),
                    "mpos": (float(m.group(2)), float(m.group(3)), float(m.group(4))),
                }
            m2 = STATUS_RE.search(line)
            if m2:
                return {"state": m2.group(1), "mpos": None}
        raise GrblError("La maquina no respondio al pedido de estado ('?').")

    def get_status(self) -> str:
        """Consulta solo el estado actual (Idle, Run, Hold, Alarm, ...) via '?'."""
        return self.get_full_status()["state"]

    def get_position_mm(self) -> tuple[float, float]:
        """Posicion absoluta actual del cabezal (X, Y) en mm, segun GRBL (MPos)."""
        status = self.get_full_status()
        if status["mpos"] is None:
            raise GrblError(
                "La maquina no devolvio posicion (MPos) en su estado. "
                "Revisa el firmware/configuracion de reportes de GRBL ($10)."
            )
        x, y, _z = status["mpos"]
        return (x, y)

    def wait_idle(self, timeout_s: float = 120.0) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            status = self.get_status()
            if status == "Idle":
                return
            if status == "Alarm":
                raise GrblAlarm("La maquina esta en estado ALARM mientras se esperaba Idle.")
            time.sleep(IDLE_POLL_INTERVAL_S)
        raise GrblError(f"Timeout esperando estado Idle (quedo en un estado no-idle > {timeout_s}s).")

    def feed_hold(self) -> None:
        """Pausa inmediata (comando en tiempo real '!'). No requiere esperar 'ok'."""
        self._require_connection().write(b"!")

    def resume(self) -> None:
        self._require_connection().write(b"~")

    def soft_reset(self) -> None:
        """Reset por software (Ctrl-X / 0x18). Detiene el trabajo en curso."""
        self._require_connection().write(b"\x18")
        time.sleep(1.5)

    def move_to_absolute(
        self, x_mm: float, y_mm: float, feed_mm_min: float, wait_idle_timeout_s: float = 30.0
    ) -> None:
        """Mueve el cabezal (laser apagado) a una posicion absoluta y espera a
        que termine. Se usa SOLO para posicionar la camara durante el escaneo
        de deteccion de la plancha (ver grid_scan.py) -- nunca para grabar: los
        movimientos de un trabajo real van todos dentro del G-code generado y
        se mandan con stream(), no con este metodo."""
        self._send_line("M5")  # laser apagado, por las dudas
        self._send_line("G90")
        self._send_line(f"G0 X{x_mm:.3f} Y{y_mm:.3f} F{feed_mm_min:.0f}")
        self.wait_idle(timeout_s=wait_idle_timeout_s)

    def move_relative(
        self, dx_mm: float, dy_mm: float, feed_mm_min: float, wait_idle_timeout_s: float = 30.0
    ) -> None:
        """Igual que move_to_absolute pero relativo a la posicion actual.
        Se usa durante la calibracion de camara (calibrate.py)."""
        self._send_line("M5")
        self._send_line("G91")
        self._send_line(f"G0 X{dx_mm:.3f} Y{dy_mm:.3f} F{feed_mm_min:.0f}")
        self._send_line("G90")
        self.wait_idle(timeout_s=wait_idle_timeout_s)

    def _send_line(self, line: str) -> None:
        ser = self._require_connection()
        ser.write((line + "\n").encode("ascii", errors="replace"))
        deadline = time.time() + LINE_TIMEOUT_S
        while time.time() < deadline:
            resp = ser.readline().decode(errors="replace").strip()
            if not resp:
                continue
            if resp.lower().startswith("ok"):
                return
            if resp.lower().startswith("error"):
                raise GrblError(f"GRBL devolvio error para la linea '{line}': {resp}")
            if resp.lower().startswith("alarm"):
                raise GrblAlarm(f"GRBL entro en ALARM al procesar '{line}': {resp}")
            # otras lineas informativas (p.ej. mensajes de arranque) se ignoran
        raise GrblError(f"Timeout esperando respuesta de GRBL para la linea '{line}'.")

    @staticmethod
    def _clean_lines(gcode: str) -> list[str]:
        cleaned = []
        for raw in gcode.splitlines():
            line = raw.split(";", 1)[0].strip()
            if line:
                cleaned.append(line)
        return cleaned

    def stream(self, gcode: str, on_progress=None) -> None:
        """Manda un bloque de G-code linea por linea, esperando 'ok' en cada una.

        on_progress, si se pasa, se llama con un SendProgress despues de cada
        linea confirmada (util para logging/beckeo del agente orquestador).
        """
        lines = self._clean_lines(gcode)
        total = len(lines)
        for i, line in enumerate(lines, start=1):
            self._send_line(line)
            if on_progress:
                on_progress(SendProgress(line_number=i, total_lines=total, line_text=line))
