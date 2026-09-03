"""Calibracion interactiva de la camara: LOCAL (pixeles -> mm alrededor del
cabezal), no una homografia de toda la mesa -- la camara de la Falcon2 esta
montada en el cabezal y se mueve con el, asi que lo que hace falta es saber
cuantos mm representa cada pixel alrededor del centro de la foto, sea cual
sea la posicion del cabezal.

Se corre UNA VEZ (o cada vez que se mueva/reenfoque la camara).

Como se usa:
  1. Cerra Falcon Design Space (el puerto serie y la camara no se pueden
     compartir con otro programa).
  2. Asegurate de tener algo con un detalle bien visible y fijo dentro del
     area de trabajo (una marca en la bandeja, un tornillo, un pedacito de
     cinta pegada) -- no hace falta que sea nada especial, solo algo que
     puedas reconocer con la vista en dos fotos parecidas.
  3. Corre este script. La maquina se va a mover un poco (sin encender el
     laser en ningun momento) y te va a pedir que marques ese mismo punto
     con un click en varias fotos.
"""
from __future__ import annotations

import sys

import cv2

from falcon_batch.calibration import compute_local_calibration, save_calibration
from falcon_batch.camera import Camera
from falcon_batch.config import load_config
from falcon_batch.grbl_sender import GrblAlarm, GrblError, GrblSender

MOVE_MM = 20.0  # distancia de cada movimiento de calibracion

_clicked_point: tuple[float, float] | None = None


def _on_mouse(event, x, y, flags, param):
    global _clicked_point
    if event == cv2.EVENT_LBUTTONDOWN:
        _clicked_point = (float(x), float(y))


def _ask_click(frame, prompt: str) -> tuple[float, float]:
    global _clicked_point
    _clicked_point = None
    window = "Calibracion"
    cv2.namedWindow(window)
    cv2.setMouseCallback(window, _on_mouse)
    print(f"\n{prompt}")
    print("(click en la ventana de imagen; 'q' para cancelar todo)")
    while _clicked_point is None:
        display = frame.copy()
        cv2.putText(
            display, prompt, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2
        )
        cv2.imshow(window, display)
        key = cv2.waitKey(20) & 0xFF
        if key == ord("q"):
            cv2.destroyAllWindows()
            raise SystemExit("Calibracion cancelada por el usuario.")
    point = _clicked_point
    cv2.destroyAllWindows()
    return point


def main() -> int:
    cfg = load_config()

    print("Conectando camara...")
    cam = Camera(cfg.camera)
    cam.open()

    print("Conectando a la maquina por puerto serie...")
    grbl = GrblSender(cfg.printer.serial_port, cfg.printer.baudrate)
    try:
        grbl.connect()
    except Exception as exc:  # noqa: BLE001
        cam.close()
        print(f"No se pudo conectar a la maquina: {exc}", file=sys.stderr)
        return 1

    try:
        status = grbl.get_status()
        print(f"Estado de la maquina: {status}")
        if status == "Alarm":
            print(
                "La maquina esta en ALARM. Desbloqueala (o hace home) desde Falcon "
                "Design Space o la consola de GRBL antes de calibrar, y volve a "
                "correr este script.",
                file=sys.stderr,
            )
            return 1

        print(
            "\nLa maquina va a moverse un poco (el laser NO se enciende en ningun "
            "momento de esta calibracion). Asegurate de que el area de movimiento "
            "este despejada.\n"
        )
        input("Presiona Enter para continuar...")

        frame0, _ = cam.capture(label="calib_0", save=False)
        p0 = _ask_click(
            frame0, "Marca con un click un punto fijo y reconocible (tornillo, marca, cinta)."
        )

        print(f"Moviendo +{MOVE_MM}mm en X...")
        grbl.move_relative(MOVE_MM, 0.0, cfg.scan.travel_feed_mm_min)
        frame_x, _ = cam.capture(label="calib_x", save=False)
        p_after_x = _ask_click(frame_x, "Marca el MISMO punto de antes, en esta nueva foto.")

        print(f"Volviendo -{MOVE_MM}mm en X...")
        grbl.move_relative(-MOVE_MM, 0.0, cfg.scan.travel_feed_mm_min)

        frame1, _ = cam.capture(label="calib_1", save=False)
        p1 = _ask_click(frame1, "Marca de nuevo el mismo punto (para el movimiento en Y).")

        print(f"Moviendo +{MOVE_MM}mm en Y...")
        grbl.move_relative(0.0, MOVE_MM, cfg.scan.travel_feed_mm_min)
        frame_y, _ = cam.capture(label="calib_y", save=False)
        p_after_y = _ask_click(frame_y, "Marca el MISMO punto de antes, en esta nueva foto.")

        print(f"Volviendo -{MOVE_MM}mm en Y...")
        grbl.move_relative(0.0, -MOVE_MM, cfg.scan.travel_feed_mm_min)

    except (GrblError, GrblAlarm) as exc:
        print(f"\nError de la maquina durante la calibracion: {exc}", file=sys.stderr)
        return 1
    finally:
        grbl.disconnect()
        cam.close()

    h, w = frame0.shape[:2]
    cal = compute_local_calibration(
        point_before_x=p0,
        point_after_x=p_after_x,
        dx_mm=MOVE_MM,
        point_before_y=p1,
        point_after_y=p_after_y,
        dy_mm=MOVE_MM,
        image_width=w,
        image_height=h,
    )
    save_calibration(cal, cfg.camera.calibration_file)

    fov_w, fov_h = cal.fov_extent_mm()
    print(f"\nCalibracion guardada en '{cfg.camera.calibration_file}'.")
    print(f"Campo de vision estimado de la camara: {fov_w:.0f} x {fov_h:.0f} mm.")
    print(
        "Si ese numero te parece muy distinto de lo que realmente ve la camara "
        "(mucho mas chico o mas grande), repeti la calibracion marcando los "
        "puntos con mas cuidado."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
