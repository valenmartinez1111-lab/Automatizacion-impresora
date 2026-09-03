# Automatizacion-impresora — Grabado en serie (Creality Falcon2 Pro S)

Agente que graba una lista de nombres en carteles, en serie, mandando G-code
**directo a la impresora por puerto serie** (protocolo GRBL), sin pasar por
Falcon Design Space en cada cartel.

Como la plancha de material casi nunca coincide con el area de la mesa, y no
se usan marcadores/stickers, el sistema tiene que "ver" donde quedo apoyada.
La camara de esta Falcon2 es FIJA (no se mueve con el laser) y ve toda la
mesa de una sola foto, asi que el agente:

1. Saca una foto de la mesa vacia (referencia).
2. Saca una foto con la plancha puesta.
3. Detecta el rectangulo de la plancha por diferencia entre ambas fotos (no
   depende del color del material).
4. Le pide a un modelo de vision (Claude) que confirme que esa deteccion es
   correcta y segura antes de dejar generar o enviar nada al laser.
5. Genera el texto de cada nombre con la fuente real (Arial), acomoda varios
   carteles en grilla dentro de la plancha detectada, y arma el G-code ya
   rotado/trasladado a la posicion real de la plancha sobre la mesa.
6. Envia ese G-code por el puerto serie y espera a que termine.
7. Repite para la proxima plancha hasta terminar la lista.

Todo esto lo orquesta un agente (Claude con tool-use): decide el orden de
pasos y como reaccionar ante problemas (una verificacion rechazada, una
plancha muy chica, una alarma de la maquina), pero **las reglas de seguridad
estan en el codigo, no solo en las instrucciones del agente**: no se puede
generar G-code sin una verificacion de vision aprobada para la deteccion
actual, y no se puede enviar nada a la maquina si esa verificacion no
corresponde exactamente a lo que se genero.

> Esta sesion de Claude Code corrio en la nube: no tiene acceso a tu
> impresora, camara ni puerto USB. Todo este proyecto lo tenes que correr vos,
> en la computadora Windows conectada fisicamente a la Falcon2 Pro S.

## Advertencia de seguridad

Este software controla un laser de verdad. Los chequeos automaticos (limites
de mesa, verificacion de vision, rectangularidad de la deteccion) reducen el
riesgo de errores, pero **no reemplazan la supervision humana**:

- No dejes la maquina desatendida mientras corre un trabajo.
- Tene un extintor apropiado a mano.
- Empeza siempre con `--dry-run` y con materiales de prueba baratos hasta
  confiar en la calibracion y en los parametros de grabado.
- Si algo se ve raro, frena: `Ctrl+C` en la consola hace un `feed hold` +
  `soft reset` de la maquina.

## 1. Instalacion

Requiere Python 3.10 o mas nuevo.

```
pip install -r requirements.txt
```

## 2. Conseguir una API key de Anthropic

El agente orquestador y el chequeo de vision usan la API de Claude (esto es
independiente de Claude Code / esta sesion).

1. Anda a https://console.anthropic.com/ y crea una cuenta (o iniciá sesión).
2. En la seccion "API Keys", crea una key nueva.
3. Configurala como variable de entorno en Windows. En PowerShell:
   ```
   setx ANTHROPIC_API_KEY "tu-key-aca"
   ```
   Despues de esto, abri una consola nueva para que tome el valor.

Esto tiene un costo de uso por request (no es parte de tu plan de Claude
Code/claude.ai). Cada verificacion de foto y cada paso del agente consume
unos pocos tokens; para lotes de decenas de carteles el costo es bajo, pero
es tuyo revisarlo en la consola de Anthropic.

## 3. Configuracion de la maquina

```
copy config.example.yaml config.yaml
```

Editar `config.yaml` y completar, como minimo:

- `printer.serial_port`: Administrador de dispositivos de Windows > Puertos
  (COM y LPT), con la Falcon2 conectada. Ej: `"COM3"`.
- `printer.bed_width_mm` / `bed_height_mm`: abrir Falcon Design Space > Laser
  > Configuracion del equipo > "Tamano de mesa de trabajo", y **despues
  cerrar FDS** (el puerto serie y la camara no se pueden compartir).
- `printer.max_laser_s_value`: en la consola de GRBL (se puede usar la
  consola de FDS antes de cerrarlo, o cualquier terminal serie) mandar `$$`
  y anotar el valor de `$30=`.
- `font.path`: ya viene apuntando a `C:/Windows/Fonts/arial.ttf`. Si tu
  Windows no tiene Arial ahi, ajustalo.

Las plantillas de cartel (`sign_templates`) y los parametros de grabado
(`engrave`) ya vienen precargados con tus medidas (carteles 30x15 y 30x18) y
un punto de partida razonable de velocidad/potencia para texto — ajustalos
si tus pruebas dan mejor resultado con otros valores.

## 4. Calibrar la camara (una sola vez)

Con Falcon Design Space **cerrado**:

```
python list_cameras.py     # para encontrar el device_index correcto, si hace falta
python calibrate.py
```

`calibrate.py` te va a mostrar la imagen de la camara (probablemente con
distorsion "ojo de pez" en los bordes, si tu camara es gran angular). Click
en 6 o mas puntos, bien repartidos entre el centro y los bordes de la
imagen, cuya posicion en mm sobre la mesa conozcas con precision — las
opciones mas practicas:

- Puntos de la bandeja/panal que puedas medir con una regla desde el origen
  (0,0) de la mesa (esquina inferior izquierda, igual que en FDS).
- Pedacitos de cinta pegados en varias posiciones medidas de la bandeja.

Por cada click, la terminal te va a pedir las coordenadas en mm de ese
punto. Al final se guarda `calibration/camera_calibration.json` y se informa
el error de reproyeccion — con una lente sin distorsion deberia ser bajo
(menos de 1-2mm); con una lente muy gran angular puede ser mas alto, pero si
da muy alto (mas de 5mm), repeti marcando mas puntos y mejor repartidos.

Repetir esta calibracion si se mueve, reenfoca, o reemplaza la camara.

## 5. Lista de nombres

`names.csv` (o el archivo que quieras, con columna `name`):

```csv
name
Valentin
Maria
Jose Luis
```

Para plantillas de **dos renglones** (`*_2lineas`), separa los renglones con
`|` en la misma celda:

```csv
name
Maria|Gonzalez
```

## 6. Correrlo

Primero en modo simulado (no toca la maquina, no prende el laser):

```
python run_batch.py --names names.csv --template 30x15 --dry-run
```

Revisa en `logs/photos/` las fotos y las detecciones de plancha (con el
rectangulo superpuesto), y en `logs/gcode/` el G-code generado, antes de
confiar en el resultado.

Cuando estes conforme, en serio (con Falcon Design Space cerrado):

```
python run_batch.py --names names.csv --template 30x15
```

El agente te va a ir guiando por consola: cuando pide confirmar que la mesa
esta vacia, cuando pedir que coloques una plancha nueva, etc. Cada plancha
puede llevar varios carteles nesteados en grilla automaticamente, segun lo
que entre.

## Estructura del proyecto

```
falcon_batch/
  config.py          # carga y valida config.yaml
  camera.py           # captura de camara USB
  calibration.py       # homografia pixeles -> mm (multiples puntos, camara fija)
  sheet_detector.py    # deteccion de plancha por diferencia de fondo
  vision_agent.py       # verificacion de seguridad con Claude vision
  text_to_paths.py      # texto -> contornos vectoriales (fuente real)
  sign_layout.py         # aplica medidas de la plantilla de cartel
  nesting.py              # grilla de carteles dentro de la plancha
  fill.py                  # relleno solido de letras (modo "fill")
  gcode_generator.py        # arma el G-code final, con limites de mesa
  grbl_sender.py              # streaming del G-code por puerto serie
  tools.py                     # "tools" del agente + reglas de seguridad
  batch_agent.py                 # bucle del agente orquestador (Claude tool-use)
calibrate.py            # calibracion interactiva de camara
list_cameras.py          # utilitario para encontrar el indice de camara
console.py                # consola cruda GRBL, para diagnostico
run_batch.py               # punto de entrada
config.example.yaml          # plantilla de configuracion
names.example.csv              # ejemplo de lista de nombres
```

## Limitaciones conocidas / ideas para seguir

- La calibracion usa una homografia (transformacion proyectiva), que no
  corrige perfectamente una distorsion fuerte de lente "ojo de pez" -- con
  muchos puntos bien repartidos el resultado es utilizable, pero si tu
  camara tiene mucha distorsion, la precision puede ser algo peor cerca de
  los bordes de la imagen que cerca del centro.
- Un mismo trabajo (`run_batch.py`) procesa nombres con **una sola
  plantilla/tamano de cartel** por corrida — para mezclar tamanos, correr el
  comando varias veces, una por tamano.
- El nesting es una grilla simple (todos los carteles del mismo tamano,
  mismo margen y espaciado); no reordena ni rota carteles individualmente
  para aprovechar mejor una plancha de forma irregular.
- El "cap height" usado para escalar el texto a la altura configurada es una
  aproximacion (~0.716 del em, tipico de fuentes sans-serif); para maxima
  fidelidad con lo que ves en Falcon Design Space, revisa y ajusta
  `text_height_mm` en `config.yaml` con una pieza de prueba.
- El modo `fill` (relleno solido) usa lineas horizontales con regla
  par-impar; no hace una pasada de contorno adicional despues del relleno
  (algunos flujos de trabajo prefieren repasar el borde para que quede mas
  nitido — se puede agregar corriendo el mismo lote dos veces, una en modo
  `line` con potencia baja sobre el contorno).

## Resolucion de problemas

- **"No se pudo abrir la camara"**: cerra Falcon Design Space (o cualquier
  otro programa que use la camara) y volve a intentar.
- **El puerto serie no conecta / "Access denied"**: mismo motivo, algo mas
  tiene el puerto COM abierto (FDS, el Monitor Serie de otra herramienta,
  etc.).
- **La deteccion de plancha falla seguido**: revisa la iluminacion (evitar
  sombras duras o reflejos fuertes) y que la foto de referencia sea
  realmente de la mesa vacia, sacada con la misma luz.
- **La imagen de la camara sale completamente negra**: baja
  `camera.capture_width`/`capture_height` en `config.yaml` (probar 640x480 o
  1280x720) -- algunas camaras UVC devuelven cuadros negros con OpenCV si se
  les pide una resolucion que no soportan.
- **El error de reproyeccion de la calibracion da muy alto**: repeti
  marcando mas puntos, mejor repartidos entre el centro y los bordes de la
  imagen (no todos amontonados en un mismo sector).
- **La maquina se mueve menos de lo que se le pide**: revisa con
  `python console.py` y el comando `$$` los parametros `$100`/`$101`
  (pasos por mm en X/Y) -- deberian ser los de fabrica de tu maquina; si son
  muy distintos de lo esperado, puede ser un problema de configuracion de
  GRBL en si, no de este software.
- **El texto sale mas chico de lo esperado**: revisa en el log si
  `names_shrunk_to_fit_width` aparece con ese nombre — significa que el
  ancho de texto excedia `max_line_width_mm` de la plantilla y se redujo
  para que entre.
