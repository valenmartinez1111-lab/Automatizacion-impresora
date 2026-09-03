"""Calibracion interactiva de la camara: pixeles -> mm de la mesa.

La camara de la Falcon2 Pro S es FIJA (no se mueve) y ve toda la mesa de una
sola foto (con distorsion tipo ojo de pez en los bordes). No hace falta
mover la maquina para nada: se calibra marcando con clicks, en UNA sola
foto, varios puntos cuya posicion real en mm sobre la mesa conozcas
(medidos con una regla).

Se corre UNA VEZ (o cada vez que se mueva/reenfoque la camara).

La conversion final combina una interpolacion local entre los puntos que
marques (mas precisa) con una homografia de respaldo para lo que quede
fuera del area cubierta por esos puntos. Por eso IMPORTA MUCHO que los
puntos lleguen hasta las esquinas/bordes reales de la zona donde vas a
apoyar material -- si el material cae fuera del area cubierta, se usa la
homografia de respaldo, menos precisa.

Como se usa:
  1. Cerra Falcon Design Space (la camara no puede estar abierta por dos
     programas a la vez).
  2. Antes de correr el script, marca fisicamente 9 o mas puntos sobre la
     bandeja (pedacitos de cinta, por ejemplo). Repartilos para que lleguen
     bien hasta las 4 esquinas de la zona util de la bandeja (donde vas a
     apoyar material), no solo el centro -- por ejemplo, una grilla de 3x3
     cubriendo todo el rectangulo util. Con una regla, medi la posicion en
     mm de cada uno, tomando como origen (0,0) la esquina inferior izquierda
     de la mesa (la misma que usa Falcon Design Space -- la confirmaste
     antes en "Origen del laser"). Anota esas medidas en un papel antes de
     arrancar, para no tener que medir con la ventana de la camara abierta.
  3. Corre este script. Se abre una ventana con la foto de la mesa.
  4. Click en cada punto de referencia, EN EL ORDEN que anotaste (la ventana
     se cierra sola despues del ultimo punto), y cuando te lo pida escribi
     en la consola las coordenadas en mm de cada uno.
  5. Se calcula la calibracion y se guarda en calibration/camera_calibration.json.
"""
from __future__ import annotations

import sys

import cv2

from falcon_batch.calibration import compute_homography, save_calibration
from falcon_batch.camera import Camera
from falcon_batch.config import load_config

TARGET_POINTS = 9  # se para solo al llegar a esta cantidad, sin depender de ninguna tecla

_clicked_points: list[tuple[float, float]] = []


def _on_mouse(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN and len(_clicked_points) < TARGET_POINTS:
        _clicked_points.append((float(x), float(y)))
        print(f"  Punto {len(_clicked_points)}/{TARGET_POINTS} marcado en pixel ({x}, {y})")


def main() -> int:
    cfg = load_config()

    print("Abriendo camara...")
    cam = Camera(cfg.camera)
    cam.open()
    try:
        frame, _ = cam.capture(label="calibracion", save=False)
    finally:
        cam.close()

    h, w = frame.shape[:2]
    window = f"Calibracion - marca los {TARGET_POINTS} puntos, en orden"
    cv2.namedWindow(window)
    cv2.setMouseCallback(window, _on_mouse)

    print(f"\nHace click en cada uno de los {TARGET_POINTS} puntos de referencia sobre la")
    print("ventana de imagen, en el mismo orden en que anotaste sus medidas.")
    print(f"La ventana se cierra sola apenas marques los {TARGET_POINTS} puntos.\n")

    while len(_clicked_points) < TARGET_POINTS:
        display = frame.copy()
        for i, (px, py) in enumerate(_clicked_points):
            cv2.circle(display, (int(px), int(py)), 6, (0, 0, 255), -1)
            cv2.putText(
                display,
                str(i + 1),
                (int(px) + 8, int(py) - 8),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 0, 255),
                2,
            )
        cv2.imshow(window, display)
        cv2.waitKey(20)
    cv2.destroyAllWindows()

    if len(_clicked_points) < 4:
        print(f"Se marcaron solo {len(_clicked_points)} puntos. Se necesitan al menos 4. Abortando.")
        return 1

    points_mm: list[tuple[float, float]] = []
    print("\nAhora ingresa la posicion en mm de cada punto marcado, en el mismo orden")
    print("(origen: esquina inferior izquierda de la mesa, igual que en FDS).\n")
    for i, (px, py) in enumerate(_clicked_points):
        while True:
            raw = input(f"Punto {i + 1} (pixel {px:.0f},{py:.0f}) -> mm 'X,Y': ").strip()
            try:
                x_str, y_str = raw.split(",")
                x_mm, y_mm = float(x_str), float(y_str)
                break
            except ValueError:
                print("  Formato invalido. Ejemplo: 0,0  o  200,150.5")
        if not (0 <= x_mm <= cfg.printer.bed_width_mm) or not (
            0 <= y_mm <= cfg.printer.bed_height_mm
        ):
            print(
                f"  Aviso: ({x_mm},{y_mm}) esta fuera del area de la mesa "
                f"configurada ({cfg.printer.bed_width_mm}x{cfg.printer.bed_height_mm}mm). "
                "Se guarda igual, pero revisa que sea correcto."
            )
        points_mm.append((x_mm, y_mm))

    cal = compute_homography(_clicked_points, points_mm, image_width=w, image_height=h)
    save_calibration(cal, cfg.camera.calibration_file)

    print(f"\nCalibracion guardada en '{cfg.camera.calibration_file}'.")
    print(f"Error de reproyeccion promedio: {cal.reprojection_error_mm:.2f} mm")
    if cal.reprojection_error_mm > 5.0:
        print(
            "ATENCION: el error es alto (>5mm). Con camaras con mucha distorsion "
            "'ojo de pez' esto puede pasar si los puntos estan muy amontonados o "
            "las medidas tienen error. Repeti la calibracion con mas puntos, mejor "
            "medidos y repartidos entre el centro y los bordes de la imagen."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
