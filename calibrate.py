"""Calibracion interactiva de la camara: pixeles -> mm de la mesa.

La camara de la Falcon2 Pro S es FIJA (no se mueve con el cabezal) y ve toda
la mesa de una sola foto (con distorsion tipo ojo de pez en los bordes). Por
eso esta calibracion NO mueve la maquina para nada: es una sola foto, en la
que marcas varios puntos de referencia cuya posicion real en mm conoces.

Se corre UNA VEZ (o cada vez que se mueva/reenfoque la camara).

Como se usa:
  1. Cerra Falcon Design Space (la camara no puede estar abierta por dos programas).
  2. Elegi 6 o mas puntos de referencia, bien distribuidos por toda la imagen
     (no todos amontonados en el centro), cuya posicion en mm sobre la mesa
     conozcas con precision. Las opciones mas practicas:
       a) Puntos de la bandeja/panal que puedas medir con una regla desde el
          origen (0,0) de la mesa, esquina inferior izquierda (igual que en
          Falcon Design Space).
       b) Pedacitos de cinta pegados en distintas posiciones medidas de la
          bandeja.
     Cuantos mas puntos marques, y mas repartidos por la imagen (centro Y
     bordes), mejor le va a la calibracion a la hora de compensar la
     distorsion de la lente.
  3. Corre este script. Se abre una ventana con la imagen de la camara.
  4. Click en cada punto de referencia, EN ORDEN, y cuando te lo pida escribi
     en la consola las coordenadas en mm de ese punto (X,Y con el origen en
     la esquina inferior izquierda de la mesa).
  5. Al terminar (minimo 4 puntos, 'q' para cerrar), se calcula la homografia
     y se guarda en calibration/camera_calibration.json.
"""
from __future__ import annotations

import sys

import cv2

from falcon_batch.calibration import compute_homography, save_calibration
from falcon_batch.camera import Camera
from falcon_batch.config import load_config

_clicked_points: list[tuple[float, float]] = []


def _on_mouse(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN:
        _clicked_points.append((float(x), float(y)))
        print(f"  Punto {len(_clicked_points)} marcado en pixel ({x}, {y})")


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
    window = "Calibracion - click en cada punto de referencia, 'q' para terminar"
    cv2.namedWindow(window)
    cv2.setMouseCallback(window, _on_mouse)

    print("\nHace click en cada punto de referencia sobre la ventana de imagen.")
    print("Apreta 'q' en la ventana cuando hayas marcado todos los puntos (minimo 4,")
    print("recomendado 6 o mas, bien repartidos por la imagen).\n")

    while True:
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
        key = cv2.waitKey(20) & 0xFF
        if key == ord("q"):
            break
    cv2.destroyAllWindows()

    if len(_clicked_points) < 4:
        print(f"Se marcaron solo {len(_clicked_points)} puntos. Se necesitan al menos 4. Abortando.")
        return 1

    points_mm: list[tuple[float, float]] = []
    print("\nAhora ingresa la posicion en mm de cada punto marcado (origen: esquina")
    print("inferior izquierda de la mesa, igual que en Falcon Design Space).\n")
    for i, (px, py) in enumerate(_clicked_points):
        while True:
            raw = input(f"Punto {i + 1} (pixel {px:.0f},{py:.0f}) -> mm 'X,Y': ").strip()
            try:
                x_str, y_str = raw.split(",")
                x_mm, y_mm = float(x_str), float(y_str)
                break
            except ValueError:
                print("  Formato invalido. Ejemplo: 0,0  o  400,250.5")
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
            "muy cerca de los bordes. Repeti la calibracion con mas puntos, bien "
            "repartidos entre el centro y los bordes de la imagen."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
