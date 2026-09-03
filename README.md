# Automatizacion-impresora — Grabado en serie (Creality Falcon2 Pro S)

Agente que graba una lista de nombres en carteles, en serie, mandando G-code
**directo a la impresora por puerto serie** (protocolo GRBL), sin pasar por
Falcon Design Space en cada cartel.

Como la plancha de material casi nunca coincide con el area de la mesa, y no
se usan marcadores/stickers, el sistema tiene que "ver" donde quedo apoyada.
La camara de la Falcon2 esta montada en el cabezal (se mueve con el laser) y
solo ve una zona chica por vez, no la mesa completa -- asi que en vez de una
sola foto, el agente:

1. Mueve el cabezal por una grilla de posiciones que cubre toda la mesa
   (laser apagado todo el tiempo) y saca una foto en cada parada, con la
   mesa vacia (referencia).
2. Repite el mismo recorrido con la plancha de material ya puesta.
3. Compara parche por parche (misma posicion, antes/despues) y arma, con
   todos los pixeles que cambiaron en toda la mesa, el rectangulo de la
   plancha real (no depende del color del material).
4. Le pide a un modelo de vision (Claude) que revise las fotos de los
   parches con cambio y confirme que esa deteccion es correcta y segura
   antes de dejar generar o enviar nada al laser.
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

La camara de la Falcon2 esta montada en el cabezal, no mira toda la mesa de
punto fijo -- por eso esta calibracion NO es "click en las 4 esquinas de la
mesa". Es una calibracion **local**: cuantos mm representa cada pixel
alrededor de donde este el cabezal en un momento dado. Se mide moviendo la
maquina una distancia conocida y marcando con un click el mismo punto fisico
antes y despues de cada movimiento -- no hace falta regla ni patron impreso,
alcanza con algo reconocible a la vista (un tornillo, una marca, un pedazo
de cinta pegado en la bandeja).

Con Falcon Design Space **cerrado**:

```
python list_cameras.py     # para encontrar el device_index correcto, si hace falta
python calibrate.py
```

`calibrate.py` conecta la maquina y la camara juntas, y te va a ir guiando:
marca un punto fijo, la maquina se mueve 20mm en X (laser apagado siempre),
marca el mismo punto de nuevo, vuelve, y repite lo mismo en Y. Al final
guarda `calibration/camera_calibration.json` e informa el campo de vision
estimado de la camara en mm -- si ese numero te parece muy raro (mucho mas
chico o grande de lo que realmente ve la camara), repetila con mas cuidado
al marcar los puntos.

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

Primero en modo `--dry-run`. **Importante:** dry-run SI se conecta a la
maquina y SI mueve el cabezal para escanear la mesa con la camara (eso es
seguro: el laser esta apagado en todo momento del escaneo) -- lo unico que
evita es mandar el G-code real de grabado, que queda simulado:

```
python run_batch.py --names names.csv --template 30x15 --dry-run
```

Revisa en `logs/photos/` las fotos de cada parche escaneado (con la zona de
cambio marcada en rojo), y en `logs/gcode/` el G-code que se hubiese
mandado, antes de confiar en el resultado.

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
  calibration.py       # calibracion LOCAL pixeles -> mm (camara montada en el cabezal)
  grid_scan.py          # recorre la mesa en grilla y arma la deteccion de plancha
  sheet_detector.py      # ajuste de rectangulo + diferencia de fondo por parche
  vision_agent.py          # verificacion de seguridad con Claude vision
  text_to_paths.py          # texto -> contornos vectoriales (fuente real)
  sign_layout.py              # aplica medidas de la plantilla de cartel
  nesting.py                    # grilla de carteles dentro de la plancha
  fill.py                        # relleno solido de letras (modo "fill")
  gcode_generator.py              # arma el G-code final, con limites de mesa
  grbl_sender.py                    # movimiento + streaming G-code por puerto serie
  tools.py                            # "tools" del agente + reglas de seguridad
  batch_agent.py                        # bucle del agente orquestador (Claude tool-use)
calibrate.py            # calibracion interactiva de camara (2 movimientos + clicks)
list_cameras.py          # utilitario para encontrar el indice de camara
run_batch.py               # punto de entrada
config.example.yaml          # plantilla de configuracion
names.example.csv              # ejemplo de lista de nombres
```

## Limitaciones conocidas / ideas para seguir

- El escaneo con camara puede tardar: mientras mas chico sea el campo de
  vision real de tu camara comparado con la mesa, mas posiciones necesita la
  grilla (y mas tarda). El agente te avisa la cantidad de posiciones y el
  tiempo estimado antes de arrancar cada escaneo. Se puede ajustar en
  `config.yaml` (`scan.margin_mm`, `scan.overlap_fraction`,
  `scan.travel_feed_mm_min`) si resulta muy lento.
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
  sombras duras o reflejos fuertes) y que el escaneo de referencia sea
  realmente de la mesa vacia, sacado con la misma luz que el escaneo actual.
- **El escaneo tarda demasiado**: bajá `scan.overlap_fraction` en
  `config.yaml` (menos solape = menos posiciones), o subí
  `scan.travel_feed_mm_min` si tu maquina puede moverse mas rapido con
  seguridad.
- **La calibracion da un campo de vision (FOV) que no tiene sentido**: repetila
  marcando con mas cuidado el mismo punto fisico exacto en las dos fotos de
  cada eje (X e Y); un click desplazado unos pixeles ya distorsiona bastante
  el resultado.
- **El texto sale mas chico de lo esperado**: revisa en el log si
  `names_shrunk_to_fit_width` aparece con ese nombre — significa que el
  ancho de texto excedia `max_line_width_mm` de la plantilla y se redujo
  para que entre.
