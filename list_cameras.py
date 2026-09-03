"""Utilitario para encontrar el indice correcto de la camara en Windows.

Prueba los indices 0 a 5, muestra cada camara encontrada por 2 segundos con su
numero de indice superpuesto, para que sepas cual poner en config.yaml
(camera.device_index).

Cerra Falcon Design Space antes de correr esto.
"""
import cv2

FOUND = []
for idx in range(6):
    cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap.release()
        continue
    ok, frame = cap.read()
    if ok and frame is not None:
        FOUND.append(idx)
        cv2.putText(
            frame,
            f"device_index = {idx}  (cerrar ventana o ESC para seguir)",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (0, 0, 255),
            2,
        )
        cv2.imshow(f"camara {idx}", frame)
        cv2.waitKey(2000)
        cv2.destroyAllWindows()
    cap.release()

print("Indices de camara detectados:", FOUND)
if not FOUND:
    print("No se detecto ninguna camara. Revisa conexiones y que FDS este cerrado.")
