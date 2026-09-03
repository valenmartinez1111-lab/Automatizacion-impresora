"""Punto de entrada del grabado en serie.

Uso:
  python run_batch.py --names names.csv --template 30x15
  python run_batch.py --names names.csv --template 30x18_2lineas
  python run_batch.py --names names.csv --template 30x15 --dry-run

--dry-run corre todo el flujo (fotos, deteccion, verificacion de vision,
nesting, generacion de G-code) pero NUNCA se conecta al puerto serie ni prende
el laser: el "envio" es simulado. Sirve para probar la deteccion de plancha y
el layout de texto sin gastar material ni arriesgar nada.

Antes de correr esto en serio:
  1. Cerrar Falcon Design Space.
  2. Haber corrido calibrate.py al menos una vez.
  3. Tener config.yaml completo (copiado de config.example.yaml).
  4. Tener la variable de entorno ANTHROPIC_API_KEY configurada.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

from falcon_batch.batch_agent import BatchAgentError, run_batch_agent
from falcon_batch.config import ConfigError, load_config
from falcon_batch.tools import BatchToolbox


def _read_names(csv_path: str, two_line: bool) -> list[str]:
    names: list[str] = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if "name" not in (reader.fieldnames or []):
            raise SystemExit(
                f"'{csv_path}' debe tener una columna 'name'. Columnas encontradas: "
                f"{reader.fieldnames}"
            )
        for row in reader:
            raw = (row.get("name") or "").strip()
            if not raw:
                continue
            if two_line:
                raw = raw.replace("|", "\n")
            names.append(raw)
    if not names:
        raise SystemExit(f"'{csv_path}' no tiene ningun nombre.")
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description="Grabado en serie en la Falcon2 Pro S")
    parser.add_argument("--names", required=True, help="CSV con columna 'name'")
    parser.add_argument(
        "--template", required=True, help="Plantilla de cartel (ver config.yaml: sign_templates)"
    )
    parser.add_argument("--config", default=None, help="Ruta a config.yaml (default: ./config.yaml)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No se conecta a la maquina; simula el envio de G-code.",
    )
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        # Hace falta siempre, dry-run incluido: tanto el agente orquestador como
        # la verificacion de vision usan la API de Claude.
        print(
            "Falta la variable de entorno ANTHROPIC_API_KEY. Ver README.md, seccion "
            "'Conseguir una API key de Anthropic'.",
            file=sys.stderr,
        )
        return 1

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"Error de configuracion: {exc}", file=sys.stderr)
        return 1

    if args.template not in cfg.sign_templates:
        print(
            f"Plantilla '{args.template}' no existe. Opciones: {list(cfg.sign_templates)}",
            file=sys.stderr,
        )
        return 1

    template = cfg.sign_templates[args.template]
    names = _read_names(args.names, two_line=template.max_lines > 1)

    print(f"{len(names)} nombres a procesar con la plantilla '{args.template}'.")
    if args.dry_run:
        print("*** MODO DRY-RUN: no se va a conectar a la maquina ni a prender el laser. ***")
    else:
        print(
            "ATENCION: este proceso va a controlar el laser de la maquina. Asegurate de "
            "estar presente y de poder frenarla en cualquier momento (boton de emergencia "
            "fisico, o Ctrl+C aca)."
        )
        confirm = input("Escribi 'si' para continuar: ").strip().lower()
        if confirm != "si":
            print("Cancelado.")
            return 0

    toolbox = BatchToolbox(cfg, names, args.template, dry_run=args.dry_run)
    try:
        toolbox.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"No se pudo inicializar camara/maquina: {exc}", file=sys.stderr)
        return 1

    try:
        run_batch_agent(cfg.orchestrator_agent, toolbox)
    except BatchAgentError as exc:
        print(f"\nEl agente se detuvo: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario. Frenando la maquina...")
        try:
            toolbox.emergency_stop()
        except Exception:  # noqa: BLE001
            pass
        return 130
    finally:
        toolbox.close()

    print("\n--- Resumen ---")
    result = toolbox.get_pending_names()
    print(f"Hechos: {len(result['done'])}  Pendientes: {len(result['pending'])}")
    if result["pending"]:
        print(f"Nombres que quedaron sin procesar: {result['pending']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
