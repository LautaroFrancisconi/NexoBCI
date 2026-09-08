# NexoBCI

## Control de videojuegos mediante SSVEP

NexoBCI es una aplicación de interfaz cerebro-computadora
(*Brain-Computer Interface*, BCI) orientada al control de videojuegos
y otras aplicaciones compatibles con teclado.

El sistema utiliza potenciales evocados visuales de estado estacionario
(*Steady-State Visually Evoked Potentials*, SSVEP). El usuario
observa uno de los estímulos visuales parpadeantes y el sistema procesa
la señal EEG para clasificar el estímulo observado y ejecutar la acción
asociada.

## Flujo general

```text
Estímulos visuales → SSVEP → adquisición de EEG
→ procesamiento → clasificación → comando de teclado → videojuego

`AppController` es el componente que coordina los módulos de la interfaz, la adquisición de señal, el procesamiento, la clasificación y el control del juego.

## Uso de la documentación

Esta página está preparada para [mkdocstrings](https://mkdocstrings.github.io/), que extrae la referencia de API directamente desde los docstrings del código fuente. Para generar el sitio se requiere configurar el complemento `mkdocstrings` con el manejador de Python y asegurar que la raíz del repositorio esté incluida en las rutas de búsqueda.