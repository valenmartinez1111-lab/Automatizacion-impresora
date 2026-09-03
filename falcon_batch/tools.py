"""Las 'herramientas' (tools) que el agente orquestador puede invocar.

Esta es la capa donde viven las reglas de seguridad DURAS, en codigo, no
solo en las instrucciones que se le dan al agente. La idea es que aunque el
agente "decida" mal, no pueda saltearse estos chequeos:

  - No se puede generar G-code sin una verificacion de vision APROBADA para
    la deteccion de plancha actual.
  - No se puede enviar G-code a la maquina si no se genero para la deteccion
    verificada actual (si se vuelve a detectar la plancha, la verificacion
    anterior queda invalidada y hay que repetirla).
  - Todo punto del G-code se valida contra los limites de la mesa antes de
    generarse (ver gcode_generator.py).
  - Despues de cada envio exitoso, hay que volver a detectar y verificar
    para el siguiente trabajo, aunque sea la misma plancha fisica.

batch_agent.py usa esta clase para exponerle las tools a Claude; nunca le da
al modelo acceso directo al puerto serie ni al FS.
"""
from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

from .calibration import CameraCalibration, load_calibration
from .camera import Camera
from .config import Config
from .gcode_generator import GCodeGenerationError, build_batch_gcode
from .grbl_sender import GrblAlarm, GrblError, GrblSender
from .nesting import NestingError, SignPlacement, plan_grid_nesting
from .sheet_detector import SheetDetection, SheetDetectionError, detect_sheet
from .sign_layout import SignArtwork, SignLayoutError, build_sign_artwork
from .text_to_paths import FontRenderer
from .vision_agent import VerificationResult, verify_sheet


class ToolError(Exception):
    """Error esperable de una tool (el agente puede leer el mensaje y reaccionar)."""


def _sig(detection: SheetDetection) -> tuple:
    return (
        round(detection.width_mm, 1),
        round(detection.height_mm, 1),
        round(detection.center_mm[0], 1),
        round(detection.center_mm[1], 1),
        round(detection.rotation_deg, 1),
    )


@dataclass
class JobRecord:
    timestamp: str
    names: list[str]
    template: str
    sheet_width_mm: float
    sheet_height_mm: float
    debug_image_path: str | None
    gcode_path: str
    status: str
    detail: str = ""


class BatchToolbox:
    def __init__(
        self,
        config: Config,
        pending_names: list[str],
        template_name: str,
        log_dir: str = "logs",
        anthropic_client: anthropic.Anthropic | None = None,
        dry_run: bool = False,
    ):
        if template_name not in config.sign_templates:
            raise ToolError(
                f"La plantilla '{template_name}' no existe en config.yaml. "
                f"Opciones: {list(config.sign_templates)}"
            )
        self.config = config
        self.template = config.sign_templates[template_name]
        self.pending: list[str] = list(pending_names)
        self.done: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.dry_run = dry_run

        self.log_dir = Path(log_dir)
        self.photo_dir = self.log_dir / "photos"
        self.gcode_dir = self.log_dir / "gcode"
        for d in (self.log_dir, self.photo_dir, self.gcode_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.job_records: list[JobRecord] = []

        self.calibration: CameraCalibration = load_calibration(config.camera.calibration_file)
        self.camera = Camera(config.camera, snapshot_dir=str(self.photo_dir))
        self.font_renderer = FontRenderer(config.font_path)
        self.anthropic_client = anthropic_client or anthropic.Anthropic()

        self.grbl: GrblSender | None = None
        if not dry_run:
            self.grbl = GrblSender(config.printer.serial_port, config.printer.baudrate)

        # estado del "trabajo en curso" (una plancha)
        self._reference_frame = None
        self._current_frame = None
        self.last_detection: SheetDetection | None = None
        self.last_verification: VerificationResult | None = None
        self._verified_signature: tuple | None = None
        self.last_nesting = None
        self.last_placed_signs: list[tuple[SignArtwork, SignPlacement]] | None = None
        self._gcode_job = None
        self._gcode_signature: tuple | None = None

    # -- ciclo de vida -----------------------------------------------------

    def connect(self) -> None:
        self.camera.open()
        if self.grbl is not None:
            self.grbl.connect()

    def close(self) -> None:
        self.camera.close()
        if self.grbl is not None:
            self.grbl.disconnect()

    # -- tools ---------------------------------------------------------------

    def get_pending_names(self) -> dict:
        return {"pending": list(self.pending), "done": list(self.done), "failed": list(self.failed)}

    def ask_human(self, question: str) -> dict:
        print(f"\n[AGENTE] {question}")
        answer = input("[VOS] > ")
        return {"human_response": answer}

    def capture_reference_photo(self) -> dict:
        frame, path = self.camera.capture(label="referencia_mesa_vacia")
        self._reference_frame = frame
        # cualquier deteccion/verificacion anterior deja de ser valida
        self.last_detection = None
        self._verified_signature = None
        return {"photo_path": path}

    def capture_current_photo(self) -> dict:
        if self._reference_frame is None:
            raise ToolError(
                "Todavia no se capturo la foto de referencia de la mesa vacia "
                "(llama a capture_reference_photo primero)."
            )
        frame, path = self.camera.capture(label="mesa_con_material")
        self._current_frame = frame
        self.last_detection = None
        self._verified_signature = None
        return {"photo_path": path}

    def detect_sheet_on_table(self) -> dict:
        if self._reference_frame is None or self._current_frame is None:
            raise ToolError(
                "Faltan fotos: se necesita capture_reference_photo y "
                "capture_current_photo antes de detectar la plancha."
            )
        debug_path = str(
            self.photo_dir / f"deteccion_{datetime.datetime.now():%Y%m%d_%H%M%S_%f}.jpg"
        )
        try:
            detection = detect_sheet(
                self._reference_frame,
                self._current_frame,
                self.calibration,
                self.config.printer.bed_width_mm,
                self.config.printer.bed_height_mm,
                debug_output_path=debug_path,
            )
        except SheetDetectionError as exc:
            raise ToolError(str(exc)) from exc

        self.last_detection = detection
        self._verified_signature = None  # hay que verificar esta deteccion nueva
        return {
            "width_mm": round(detection.width_mm, 1),
            "height_mm": round(detection.height_mm, 1),
            "center_mm": [round(c, 1) for c in detection.center_mm],
            "rotation_deg": round(detection.rotation_deg, 1),
            "rectangularity": round(detection.rectangularity, 2),
            "debug_image_path": detection.debug_image_path,
        }

    def verify_sheet_with_vision(self) -> dict:
        if self.last_detection is None:
            raise ToolError("No hay ninguna deteccion pendiente de verificar.")
        if self.last_detection.debug_image_path is None:
            raise ToolError("La deteccion no genero imagen de depuracion para verificar.")

        result = verify_sheet(
            self.config.vision_agent,
            self.last_detection.debug_image_path,
            self.last_detection,
            client=self.anthropic_client,
        )
        self.last_verification = result
        if result.approved:
            self._verified_signature = _sig(self.last_detection)
        else:
            self._verified_signature = None

        return {
            "approved": result.approved,
            "confidence": round(result.confidence, 2),
            "reason": result.reason,
            "concerns": result.concerns,
        }

    def plan_nesting_for_pending(self) -> dict:
        if self.last_detection is None:
            raise ToolError("No hay ninguna plancha detectada todavia.")
        if not self.pending:
            raise ToolError("No quedan nombres pendientes.")

        try:
            nesting = plan_grid_nesting(
                self.last_detection.width_mm,
                self.last_detection.height_mm,
                self.template.width_mm,
                self.template.height_mm,
                self.config.grid_layout.margin_from_sheet_edge_mm,
                self.config.grid_layout.spacing_between_signs_mm,
                len(self.pending),
            )
        except NestingError as exc:
            raise ToolError(str(exc)) from exc

        self.last_nesting = nesting
        return {
            "columns": nesting.columns,
            "rows": nesting.rows,
            "capacity": nesting.capacity,
            "used_count": nesting.used_count,
            "leftover_count": nesting.leftover_count,
            "names_in_this_job": self.pending[: nesting.used_count],
        }

    def generate_job_gcode(self) -> dict:
        if self.last_detection is None or self._verified_signature != _sig(self.last_detection):
            raise ToolError(
                "La plancha detectada actual todavia no fue verificada por "
                "verify_sheet_with_vision (o cambio despues de la verificacion). "
                "Hay que verificar antes de generar G-code."
            )
        if self.last_nesting is None:
            raise ToolError("Todavia no se planeo el nesting (plan_nesting_for_pending).")

        names_for_job = self.pending[: self.last_nesting.used_count]
        artworks: list[SignArtwork] = []
        for name in names_for_job:
            try:
                artworks.append(build_sign_artwork(name, self.template, self.font_renderer))
            except SignLayoutError as exc:
                raise ToolError(f"Error de layout para '{name}': {exc}") from exc

        placed = [
            (artworks[p.pending_index], p) for p in self.last_nesting.placements
        ]
        try:
            job = build_batch_gcode(
                placed, self.last_detection, self.config.engrave, self.config.printer
            )
        except GCodeGenerationError as exc:
            raise ToolError(str(exc)) from exc

        gcode_path = str(
            self.gcode_dir / f"job_{datetime.datetime.now():%Y%m%d_%H%M%S}.gcode"
        )
        Path(gcode_path).write_text(job.gcode, encoding="utf-8")

        self._gcode_job = job
        self._gcode_job_names = names_for_job
        self._gcode_job_path = gcode_path
        self._gcode_signature = _sig(self.last_detection)

        shrunk = [a.text for a in artworks if a.shrunk_to_fit]
        return {
            "gcode_path": gcode_path,
            "sign_count": job.sign_count,
            "total_gcode_lines": job.total_lines,
            "names_included": names_for_job,
            "names_shrunk_to_fit_width": shrunk,
        }

    def send_job_to_printer(self) -> dict:
        if self.dry_run:
            return self._send_dry_run()

        if self._gcode_job is None:
            raise ToolError("Todavia no se genero G-code (generate_job_gcode).")
        if self.last_detection is None or self._gcode_signature != _sig(self.last_detection):
            raise ToolError(
                "El G-code generado no corresponde a la deteccion de plancha actual. "
                "Volve a detectar, verificar y generar antes de enviar."
            )
        if not self.last_verification or not self.last_verification.approved:
            raise ToolError("No hay una verificacion de vision aprobada vigente.")
        if self.config.safety.require_vision_approval and self._verified_signature != _sig(
            self.last_detection
        ):
            raise ToolError("La verificacion de vision no corresponde a la deteccion actual.")

        assert self.grbl is not None
        status = self.grbl.get_status()
        if status == "Alarm":
            raise ToolError(
                "La maquina esta en estado ALARM. No se envia nada hasta que un humano "
                "la revise y la desbloquee manualmente."
            )
        if status not in ("Idle",):
            raise ToolError(f"La maquina no esta Idle (estado actual: {status}). No se envia.")

        try:
            self.grbl.stream(self._gcode_job.gcode)
            self.grbl.wait_idle(timeout_s=600.0)
        except GrblAlarm as exc:
            self._record_job("ALARM", str(exc))
            raise ToolError(f"La maquina entro en ALARM durante el envio: {exc}") from exc
        except GrblError as exc:
            self._record_job("ERROR", str(exc))
            raise ToolError(f"Error de comunicacion GRBL: {exc}") from exc

        names_sent = self._gcode_job_names
        self.done.extend(names_sent)
        self.pending = self.pending[len(names_sent):]
        self._record_job("OK", "")

        # se invalida todo el estado de "trabajo en curso": para la proxima
        # plancha (o para nesteados adicionales en la misma plancha fisica)
        # hay que detectar y verificar de nuevo.
        self._gcode_job = None
        self._gcode_signature = None
        self._verified_signature = None
        self.last_verification = None
        self.last_detection = None
        self.last_nesting = None

        return {"status": "OK", "names_sent": names_sent, "still_pending": list(self.pending)}

    def _send_dry_run(self) -> dict:
        if self._gcode_job is None:
            raise ToolError("Todavia no se genero G-code (generate_job_gcode).")
        names_sent = self._gcode_job_names
        print(f"[DRY-RUN] Se simula el envio de {len(names_sent)} carteles: {names_sent}")
        self.done.extend(names_sent)
        self.pending = self.pending[len(names_sent):]
        self._record_job("DRY_RUN", "")
        self._gcode_job = None
        self._gcode_signature = None
        self._verified_signature = None
        self.last_verification = None
        self.last_detection = None
        self.last_nesting = None
        return {"status": "DRY_RUN", "names_sent": names_sent, "still_pending": list(self.pending)}

    def get_printer_status(self) -> dict:
        if self.dry_run:
            return {"status": "Idle (dry-run)"}
        assert self.grbl is not None
        return {"status": self.grbl.get_status()}

    def emergency_stop(self) -> dict:
        if self.dry_run:
            return {"status": "dry-run, nada que frenar"}
        assert self.grbl is not None
        self.grbl.feed_hold()
        self.grbl.soft_reset()
        return {"status": "Se envio feed-hold + soft reset a la maquina."}

    def log_note(self, note: str) -> dict:
        print(f"[NOTA DEL AGENTE] {note}")
        return {"ok": True}

    # -- utilidades internas -------------------------------------------------

    def _record_job(self, status: str, detail: str) -> None:
        d = self.last_detection
        rec = JobRecord(
            timestamp=datetime.datetime.now().isoformat(timespec="seconds"),
            names=list(getattr(self, "_gcode_job_names", [])),
            template=self.template.name,
            sheet_width_mm=d.width_mm if d else 0.0,
            sheet_height_mm=d.height_mm if d else 0.0,
            debug_image_path=d.debug_image_path if d else None,
            gcode_path=getattr(self, "_gcode_job_path", ""),
            status=status,
            detail=detail,
        )
        self.job_records.append(rec)
        log_path = self.log_dir / "job_log.jsonl"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec.__dict__, ensure_ascii=False) + "\n")
