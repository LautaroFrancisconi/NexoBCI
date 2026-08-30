
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32

VK_UP = 0x26
VK_DOWN = 0x28
VK_LEFT = 0x25
VK_RIGHT = 0x27
VK_ESCAPE = 0x1B
VK_SPACE = 0x20

KEYEVENTF_KEYUP = 0x0002


class KeyboardController:
    """
    Traduce estimulos SSVEP clasificados en eventos de teclado.

    Acciones 0 y 1 (Escape, Space) pueden funcionar en dos modos,
    definidos externamente via `toggle_action`:
        - toggle_action[i] = True  -> modo "sticky": se presiona al
          detectar el estimulo por primera vez y se mantiene sostenida
          hasta que el MISMO estimulo se vuelva a clasificar (ahi se suelta).
        - toggle_action[i] = False -> modo "momentaneo": se sostiene
          hasta que llegue un estimulo DISTINTO (ahi se suelta y se
          presiona la nueva tecla).

    Acciones 2 a 5 (flechas) siempre son momentaneas.

    Una accion toggle sostenida NO bloquea al resto: mientras esta
    sticky, puede llegar cualquier otro estimulo (momentaneo, o incluso
    otra accion toggle) y se presiona en paralelo, en su propio carril.
    """

    def __init__(self, toggle_action: list[bool] = None):
        # toggle_action[0] -> comportamiento de la accion 0 (Escape)
        # toggle_action[1] -> comportamiento de la accion 1 (Space)
        self.__toggle_action = toggle_action if toggle_action is not None else [False, False]

        # Carril toggle: puede haber mas de una accion toggle sostenida a la vez
        self.__toggled_stims = {}  # {stim: vk}

        # Carril momentaneo: una sola tecla sostenida a la vez
        self.__momentary_stim = None
        self.__momentary_key = None

        self.__actions = {
            0: VK_ESCAPE,   # Accion 1
            1: VK_SPACE,    # Accion 2
            2: VK_RIGHT,    # Flecha derecha
            3: VK_UP,       # Flecha arriba
            4: VK_LEFT,     # Flecha izquierda
            5: VK_DOWN,     # Flecha abajo
        }

    def stim_received(self, stim):
        if stim not in self.__actions:
            return

        # Si el estimulo corresponde a un toggle ya sostenido -> soltarlo
        if stim in self.__toggled_stims:
            self.__release_toggle(stim)
            return

        # Si el estimulo es una accion toggle (y no esta sostenida aun) -> presionarla en su carril
        if self.__is_toggle_action(stim):
            self.__press_toggle(stim)
            return

        # Caso momentaneo (arrows, o accion 0/1 en modo no-toggle)
        if stim == self.__momentary_stim:
            # mismo estimulo momentaneo repetido -> no hacer nada, sigue sostenida
            return

        self.__release_momentary()
        self.__press_momentary(stim)
        
    def set_toggle_action(self, index, value):
        
        self.__toggle_action[index] = value

    def __is_toggle_action(self, stim):
        return stim in (0, 1) and self.__toggle_action[stim]

    def __press_toggle(self, stim):
        vk = self.__actions[stim]
        user32.keybd_event(vk, 0, 0, 0)
        self.__toggled_stims[stim] = vk

    def __release_toggle(self, stim):
        vk = self.__toggled_stims.pop(stim, None)
        if vk is not None:
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        # al soltar el toggle, tambien se suelta lo que este activo en el carril momentaneo
        self.__release_momentary()

    def __press_momentary(self, stim):
        vk = self.__actions[stim]
        user32.keybd_event(vk, 0, 0, 0)
        self.__momentary_stim = stim
        self.__momentary_key = vk

    def __release_momentary(self):
        if self.__momentary_key is not None:
            user32.keybd_event(self.__momentary_key, 0, KEYEVENTF_KEYUP, 0)
        self.__momentary_stim = None
        self.__momentary_key = None