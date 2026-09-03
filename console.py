"""Consola cruda para mandar comandos directo a la placa GRBL de la impresora,
util para diagnostico (por ejemplo, ver la configuracion con '$$').

Cerra Falcon Design Space antes de usar esto.

Uso:
  python console.py
  > $$              (lista toda la configuracion de GRBL)
  > $100            (respuesta parcial, algunas placas la aceptan asi)
  > exit            (para salir)
"""
from __future__ import annotations

import sys

from falcon_batch.config import load_config
from falcon_batch.grbl_sender import GrblAlarm, GrblError, GrblSender


def main() -> int:
    cfg = load_config()
    print(f"Conectando a {cfg.printer.serial_port}...")
    grbl = GrblSender(cfg.printer.serial_port, cfg.printer.baudrate)
    try:
        grbl.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"No se pudo conectar: {exc}", file=sys.stderr)
        return 1

    print("Conectado. Escribi un comando GRBL (por ejemplo $$) o 'exit' para salir.\n")
    try:
        while True:
            try:
                cmd = input("> ").strip()
            except EOFError:
                break
            if cmd.lower() in ("exit", "quit", "salir"):
                break
            if not cmd:
                continue
            try:
                lines = grbl.send_raw_and_collect(cmd)
                for line in lines:
                    print(line)
            except (GrblError, GrblAlarm) as exc:
                print(f"[error] {exc}")
    finally:
        grbl.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
