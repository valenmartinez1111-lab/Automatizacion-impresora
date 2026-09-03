"""Carga y validacion de config.yaml.

Todo el resto del proyecto lee la configuracion de la maquina (puerto serie,
tamano de mesa, plantillas de cartel, parametros de grabado) a traves de este
modulo, nunca directamente del YAML, para tener un solo lugar donde se
validan los valores obligatorios antes de tocar hardware.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_ENV_VAR = "FALCON_BATCH_CONFIG"
DEFAULT_CONFIG_PATH = "config.yaml"


class ConfigError(Exception):
    pass


@dataclass
class PrinterConfig:
    serial_port: str
    baudrate: int
    bed_width_mm: float
    bed_height_mm: float
    origin: str
    max_laser_s_value: int
    max_travel_speed_mm_min: float


@dataclass
class CameraConfig:
    device_index: int
    capture_width: int
    capture_height: int
    calibration_file: str


@dataclass
class ScanConfig:
    """Como el agente barre la mesa con la camara (montada en el cabezal)
    para 'ver' toda la plancha, ya que una sola foto no alcanza."""

    margin_mm: float
    overlap_fraction: float
    travel_feed_mm_min: float


@dataclass
class VisionAgentConfig:
    model: str
    min_confidence: float


@dataclass
class OrchestratorAgentConfig:
    model: str
    max_tool_iterations: int


@dataclass
class SignTemplate:
    name: str
    width_mm: float
    height_mm: float
    font_size_pt: float
    max_line_width_mm: float
    text_height_mm: float
    line_spacing_mm: float
    max_lines: int
    preferred_line_width_mm: float | None = None


@dataclass
class EngraveParams:
    mode: str  # "line" | "fill"
    speed_mm_min: float
    power_percent: float
    passes: int
    fill_interval_mm: float = 0.15


@dataclass
class GridLayoutConfig:
    margin_from_sheet_edge_mm: float
    spacing_between_signs_mm: float


@dataclass
class SafetyConfig:
    require_vision_approval: bool


@dataclass
class Config:
    printer: PrinterConfig
    camera: CameraConfig
    scan: ScanConfig
    vision_agent: VisionAgentConfig
    orchestrator_agent: OrchestratorAgentConfig
    font_path: str
    sign_templates: dict[str, SignTemplate]
    engrave: EngraveParams
    grid_layout: GridLayoutConfig
    safety: SafetyConfig
    raw_path: str = field(default="")


def _require(d: dict, key: str, section: str):
    if key not in d or d[key] in (None, ""):
        raise ConfigError(
            f"Falta el campo obligatorio '{key}' en la seccion '{section}' de config.yaml"
        )
    return d[key]


def load_config(path: str | None = None) -> Config:
    path = path or os.environ.get(CONFIG_ENV_VAR) or DEFAULT_CONFIG_PATH
    p = Path(path)
    if not p.exists():
        raise ConfigError(
            f"No se encontro '{path}'. Copia config.example.yaml a config.yaml "
            "y completa los valores marcados como OBLIGATORIO."
        )
    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    printer_raw = raw.get("printer", {})
    printer = PrinterConfig(
        serial_port=_require(printer_raw, "serial_port", "printer"),
        baudrate=int(printer_raw.get("baudrate", 115200)),
        bed_width_mm=float(_require(printer_raw, "bed_width_mm", "printer")),
        bed_height_mm=float(_require(printer_raw, "bed_height_mm", "printer")),
        origin=printer_raw.get("origin", "bottom_left"),
        max_laser_s_value=int(printer_raw.get("max_laser_s_value", 1000)),
        max_travel_speed_mm_min=float(printer_raw.get("max_travel_speed_mm_min", 6000)),
    )
    if printer.origin != "bottom_left":
        raise ConfigError(
            "Solo se soporta origin: bottom_left por ahora (confirmado en el manual de FDS)."
        )

    camera_raw = raw.get("camera", {})
    camera = CameraConfig(
        device_index=int(camera_raw.get("device_index", 0)),
        capture_width=int(camera_raw.get("capture_width", 1920)),
        capture_height=int(camera_raw.get("capture_height", 1080)),
        calibration_file=camera_raw.get(
            "calibration_file", "calibration/camera_calibration.json"
        ),
    )

    scan_raw = raw.get("scan", {})
    scan = ScanConfig(
        margin_mm=float(scan_raw.get("margin_mm", 15.0)),
        overlap_fraction=float(scan_raw.get("overlap_fraction", 0.3)),
        travel_feed_mm_min=float(scan_raw.get("travel_feed_mm_min", 3000.0)),
    )

    va_raw = raw.get("vision_agent", {})
    vision_agent = VisionAgentConfig(
        model=va_raw.get("model", "claude-sonnet-5"),
        min_confidence=float(va_raw.get("min_confidence", 0.75)),
    )

    oa_raw = raw.get("orchestrator_agent", {})
    orchestrator_agent = OrchestratorAgentConfig(
        model=oa_raw.get("model", "claude-sonnet-5"),
        max_tool_iterations=int(oa_raw.get("max_tool_iterations", 200)),
    )

    font_path = _require(raw.get("font", {}), "path", "font")

    templates_raw = raw.get("sign_templates", {})
    if not templates_raw:
        raise ConfigError("config.yaml debe definir al menos una plantilla en 'sign_templates'.")
    sign_templates: dict[str, SignTemplate] = {}
    for name, t in templates_raw.items():
        sign_templates[name] = SignTemplate(
            name=name,
            width_mm=float(_require(t, "width_mm", f"sign_templates.{name}")),
            height_mm=float(_require(t, "height_mm", f"sign_templates.{name}")),
            font_size_pt=float(t.get("font_size_pt", 20)),
            max_line_width_mm=float(_require(t, "max_line_width_mm", f"sign_templates.{name}")),
            text_height_mm=float(_require(t, "text_height_mm", f"sign_templates.{name}")),
            line_spacing_mm=float(t.get("line_spacing_mm", 5.5)),
            max_lines=int(t.get("max_lines", 1)),
            preferred_line_width_mm=(
                float(t["preferred_line_width_mm"])
                if t.get("preferred_line_width_mm") is not None
                else None
            ),
        )

    engrave_raw = raw.get("engrave", {})
    engrave = EngraveParams(
        mode=engrave_raw.get("mode", "line"),
        speed_mm_min=float(engrave_raw.get("speed_mm_min", 1750)),
        power_percent=float(engrave_raw.get("power_percent", 22)),
        passes=int(engrave_raw.get("passes", 1)),
        fill_interval_mm=float(engrave_raw.get("fill_interval_mm", 0.15)),
    )
    if engrave.mode not in ("line", "fill"):
        raise ConfigError("engrave.mode debe ser 'line' o 'fill'.")

    grid_raw = raw.get("grid_layout", {})
    grid_layout = GridLayoutConfig(
        margin_from_sheet_edge_mm=float(grid_raw.get("margin_from_sheet_edge_mm", 5.0)),
        spacing_between_signs_mm=float(grid_raw.get("spacing_between_signs_mm", 5.0)),
    )

    safety_raw = raw.get("safety", {})
    safety = SafetyConfig(
        require_vision_approval=bool(safety_raw.get("require_vision_approval", True)),
    )

    return Config(
        printer=printer,
        camera=camera,
        scan=scan,
        vision_agent=vision_agent,
        orchestrator_agent=orchestrator_agent,
        font_path=font_path,
        sign_templates=sign_templates,
        engrave=engrave,
        grid_layout=grid_layout,
        safety=safety,
        raw_path=str(p),
    )
