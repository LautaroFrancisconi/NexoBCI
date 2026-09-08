"""Application controller that coordinates UI, acquisition and evaluation."""
import builtins
from PySide6.QtCore import QObject, QThread, QTimer
from src.app.bci_evaluator import BCIEvaluator
from src.app.bci_evaluator_widgets import (
BCIEvaluatorOperator,
BCIEvaluatorUser,
SequenceWidget,
)
from src.app.config import crear_engine, init_database
from src.app.dataclasses import ClassificationMethods, UserPreferences
from src.app.eeg_serial_iface import EEGFileInterface, EEGSerialInterface
from src.app.signal_classifier import SignalCCAClassifier, SignalTRCAClassifier
from src.app.signal_processor import SignalProcessor
from src.app.eeg_widget import EEGWidget
from src.app.electrode_head_widget import ElectrodeHeadWidget
from src.app.game_manager import GameManager
from src.app.keyboard_controller import KeyboardController
from src.app.managers import PreferencesManager, TrainingWeightsManager, UserManager
from src.app.models import Base
from src.app.psd_estimator import PSDEstimator
from src.app.psd_widget import PSDWidget
from src.app.repositories import (
PreferencesRepositorySQLAlchemy,
TrainingWeightsRepositorySQLAlchemy,
UserRepositorySQLAlchemy,
)
from src.app.signal_filters import SignalFilters
from src.app.stimulus_viewer import StimulusViewer
from src.app.training_manager import TrainingManager
from src.app.user_ui import UserUI

class AppController(QObject):
    """
    Controlador principal de la aplicación que coordina la interfaz de usuario,
    la adquisición de datos de EEG, el procesamiento de señales y la evaluación del sistema BCI.
    """

    def __init__(self, data_dir):
        """
        Inicializa el controlador principal, instancia todos los componentes del sistema 
        (UI, filtros, clasificadores, managers de persistencia, evaluadores) y establece 
        las conexiones de señales y slots de Qt.
        """
        super().__init__()
        self.__data_dir = data_dir
        self.__ui = UserUI()
        self.__user_manager = None
        self.__prefs_manager = None
        self.__weights_manager = None
        self.__actual_user = None
        self.__signal_classifier = None
        self.__eeg_iface = EEGSerialInterface()      
        self.__eeg_widget = EEGWidget()
        self.__eeg_filters = SignalFilters()
        self.__psd_estimator = PSDEstimator()
        self.__psd_widget = PSDWidget()
        self.__keyboard = KeyboardController()
        self.__processing_thread = QThread()
        self.__head_widget = ElectrodeHeadWidget()
        self.__processing_worker = SignalProcessor(self.__eeg_filters, self.__psd_estimator)
        self.__trainer = TrainingManager(base_dir=data_dir, p_filterbank=self.__eeg_filters.filterbank)
        self.__stimulus_viewer = StimulusViewer()
        self.__game_manager = GameManager()
        self.__bci_evaluator = BCIEvaluator(base_dir=data_dir, sequence=builtins.SETTINGS.sequence_evaluation)
        self.__bci_evaluator_op_interfaz = BCIEvaluatorOperator()
        self.__bci_evaluator_user_interfaz = BCIEvaluatorUser()
        self.__sequence_widget_op = SequenceWidget()
        self.__sequence_widget_user = SequenceWidget()
        self.__channels_enable = []
        
        # Application state flags.
        self.__stimulus_ready = False
        self.__training_in_progress = False
        self.__game_start_requested = False
        self.__enable_control = False
        self.__test_is_running = False
        self.__add_test_data = False
        
        # EEG signals.
        self.__eeg_iface.streamStarted.connect(self.__on_stream_started)
        self.__eeg_iface.dataDecoded.connect(self.__processing_worker.on_data_decoded)
        self.__eeg_iface.portException.connect(self.__ui.write_box_message)
        if isinstance(self.__eeg_iface, EEGSerialInterface):
            self.__eeg_iface.signalDesinc.connect(self.__ui.write_message)
        
        # Stimulus signals.
        self.__stimulus_viewer.stimuli_ready.connect(self.__on_stimuli_ready)
        self.__stimulus_viewer.flicker_started.connect(self.__trainer.add_mark)
        
        # Training signals.
        self.__trainer.trial_status.connect(self.__ui.write_message)
        self.__trainer.stimulus_indicate.connect(self.__stimulus_viewer.stimulus_indicate)
        self.__trainer.stimulus_on.connect(self.__stimulus_viewer.start_flicker)
        self.__trainer.training_finish.connect(self.__on_training_finish)
        
        # Electrode widget signals.
        self.__head_widget.channel_toggled.connect(self.__on_channel_toggled)
        
        # Processing signals.
        self.__processing_worker.filteredData.connect(self.__trainer.add_sample)
        self.__processing_worker.filteredData.connect(self.__eeg_widget.add_filtered_signal)
        self.__processing_worker.psdData.connect(self.__psd_widget.add_psd)
        self.__processing_worker.classificationResult.connect(self.__on_classification_result)
        self.__processing_worker.errorOccurred.connect(self.__ui.write_box_message)
        
        # Main UI requests.
        self.__ui.start_streaming_request.connect(self.__start_streaming)
        self.__ui.stop_streaming_request.connect(self.__stop_streaming)
        self.__ui.close_request.connect(self.__on_close_request)
        self.__ui.new_user_request.connect(self.__on_new_user_request)
        self.__ui.request_user_preferences.connect(self.__on_request_user_preferences)
        self.__ui.remove_user_request.connect(self.__on_remove_user_request)
        self.__ui.save_user_preferences_request.connect(self.__on_save_user_preferences_request)
        self.__ui.user_list_request.connect(lambda: self.__ui.load_users(self.__user_manager.get_all_users()))
        self.__ui.chk_toggle_space.connect(self.__keyboard.set_toggle_action)
        
        # Game and analysis page requests.
        self.__ui.start_training_request.connect(self.__on_start_training_request)
        self.__ui.start_test_request.connect(self.__on_start_test_request)
        self.__ui.game_list_request.connect(lambda: self.__ui.game_list_update(self.__game_manager.list_games()))
        self.__ui.bandpass_request.connect(lambda enabled: self.__eeg_filters.add_parameters(bandpass_enabled=enabled))
        self.__ui.notch_request.connect(lambda enabled: self.__eeg_filters.add_parameters(notch_enabled=enabled))
        self.__ui.average_request.connect(lambda enabled: self.__eeg_filters.add_parameters(media_enabled=enabled))
        self.__ui.eeg_speed_request.connect(self.__on_eeg_speed_changed)
        self.__ui.eeg_scale_request.connect(self.__on_scale_request)
        self.__ui.eeg_amplitude_request.connect(self.__on_eeg_amplitude_changed)
        self.__ui.psd_mode_request.connect(self.__psd_widget.set_psd_mode)
        self.__ui.psd_channel_request.connect(self.__psd_estimator.set_channel_index)
        self.__ui.start_game_request.connect(self.__on_start_game_request)
        self.__ui.stop_game_request.connect(self.__on_stop_game_request)
        self.__ui.psd_graph_request.connect(self.__on_psd_graph_request)
        self.__ui.overlap_request.connect(self.__psd_estimator.load_overlap)
        self.__ui.windows_welch_request.connect(self.__psd_estimator.load_time_windows)
        self.__ui.enable_control_request.connect(self.__on_enable_control_changed)
        self.__ui.enable_classify_request.connect(self.__processing_worker.set_enable_classify)

    def start_app(self):
        """
        Inicia los servicios principales de la aplicación: mueve el procesador de señales 
        a un hilo secundario dedicado, arranca dicho hilo, inicializa las bases de datos 
        de persistencia y finalmente despliega la interfaz gráfica de usuario.
        """
        self.__processing_worker.moveToThread(self.__processing_thread)
        self.__processing_thread.start()
        self.__user_manager, self.__prefs_manager, self.__weights_manager = self.__initialize_database()
        self.__ui.start_ui()

    def __on_close_request(self):
        """
        Maneja el cierre de la aplicación. Detiene el streaming de datos, 
        solicita la finalización del hilo de procesamiento de manera segura y espera 
        a que este termine antes de cerrar.        
        """
        self.__stop_streaming()
        self.__processing_thread.quit()
        self.__processing_thread.wait()

    def __start_streaming(self):        
        """
        Inicia la adquisición y el flujo de señales EEG. Activa el worker de procesamiento, 
        recupera las preferencias del usuario actual para conocer los canales a utilizar 
        e inicia la conexión con la interfaz de adquisición de datos.
        """ 
        self.__processing_worker.start()
        prefs = self.__prefs_manager.get_user_preferences(self.__actual_user)
        channels_index = list(prefs.channels.keys())
        self.__eeg_iface.begin_connection(channels_index)
        self.__processing_worker.set_psd_enabled(True)

    def __stop_streaming(self):
        """
        Detiene el flujo de datos de la aplicación: cierra la conexión 
        con la interfaz de EEG, detiene el worker de procesamiento, cierra el juego en 
        ejecución y apaga los estímulos visuales.
        """
        self.__eeg_iface.end_connection()
        self.__processing_worker.stop()
        self.__processing_worker.set_psd_enabled(False)
        self.__game_manager.close_game()
        self.__stimulus_viewer.stop_stim()

    def __configure_filters(self, prefs: UserPreferences):
        """
        Configura las frecuencias de corte de los filtros de señal de EEG 
        basándose en las frecuencias de los estímulos visuales activos configurados 
        en las preferencias del usuario.

        Parámetros
        ----------
        prefs : UserPreferences
            Objeto que contiene las configuraciones y preferencias del usuario actual, 
            incluyendo la lista de estímulos y sus frecuencias.

        Retorna
        -------
        Ninguno
        """
        list_freqs = [stim.freq for stim in prefs.stimulus]
        self.__eeg_filters.stop()
        self.__eeg_filters.add_parameters(low_freq=min(list_freqs), high_freq=max(list_freqs))

    def __initialize_database(self):
        """
        Inicializa el motor de la base de datos (SQLAlchemy), crea las tablas necesarias 
        si no existen a partir de los modelos de datos, y abstrae el acceso mediante repositorios 
        creando los managers de usuarios, preferencias y pesos de entrenamiento.

        Retorna
        -------
        tuple
            Una tupla con tres elementos: `(UserManager, PreferencesManager, TrainingWeightsManager)`.
        """
        init_database(self.__data_dir)
        engine_factory = crear_engine()
        engine = engine_factory.kw["bind"]
        Base.metadata.create_all(bind=engine)
        session = engine_factory()
        user_repo = UserRepositorySQLAlchemy(session)
        prefs_repo = PreferencesRepositorySQLAlchemy(session)
        weights_repo = TrainingWeightsRepositorySQLAlchemy(session)
        user_manager = UserManager(user_repo)
        prefs_manager = PreferencesManager(prefs_repo)
        weights_manager = TrainingWeightsManager(weights_repo)
        return user_manager, prefs_manager, weights_manager

    def __on_stream_started(self, started):
        """
        Slot que reacciona al estado de inicio del stream de datos EEG. Actualiza las banderas 
        del sistema, notifica al usuario en la interfaz gráfica y habilita las opciones 
        de entrenamiento en caso de éxito.

        Parámetros
        ----------
        started : bool
            Verdadero si la conexión y el stream se iniciaron correctamente, Falso en caso contrario.

        Retorna
        -------
        Ninguno
        """
        if started:
            self.__ui.write_message("Stream iniciado")
            self.__ui.enable_training()

    def __on_new_user_request(self, username):
        """
        Registra un nuevo usuario en el sistema. Le asigna las configuraciones predeterminadas, 
        actualiza la lista visual de usuarios y maneja excepciones en caso de nombres duplicados 
        o fallos de persistencia.

        Parámetros
        ----------
        username : str
            Nombre de usuario único que se desea registrar.

        Retorna
        -------
        Ninguno
        """
        new_user = None
        try:
            new_user = self.__user_manager.register_new_user(username)
            self.__prefs_manager.save_or_update_preferences(
                new_user.id,
                builtins.SETTINGS.default_user_preferences,
            )
            self.__ui.load_users(self.__user_manager.get_all_users())
        except Exception:
            if new_user is not None:
                self.__user_manager.remove_user(new_user.id)
            self.__ui.write_box_message(
                f"No se pudo crear el usuario: {username}. Verifique que el nombre no este en uso."
            )

    def __on_request_user_preferences(self, user_id):
        """
        Carga y aplica todas las preferencias del usuario seleccionado en los distintos 
        módulos del sistema (canales, gráficos, clasificador, evaluador).

        Parámetros
        ----------
        user_id : int
            Identificador único del usuario cuyas preferencias se van a cargar.

        Retorna
        -------
        Ninguno
        """
        self.__ui.enabled_button_filters(True)
        user = self.__user_manager.get_user(user_id)
        
        # 1. Reset e inicialización de datos de usuario
        self.__head_widget.clear()
        self.__psd_estimator.stop()
        self.__actual_user = user_id
        prefs = self.__prefs_manager.get_user_preferences(user_id)
        self.__ui.load_user_preferences(prefs)
        self.__configure_filters(prefs)
        self.__trainer.update_data(prefs)
        
        # 2. Configurar la interfaz de los canales (Head Widget)
        self.__setup_channels_and_head_widget(prefs)
        
        # 3. Configurar los gráficos de EEG y PSD
        self.__setup_graphs_and_estimators(prefs)
        
        # 4. Configurar el clasificador (CCA / eTRCA)
        self.__setup_classifier(prefs)
        
        # 5. Configurar el evaluador y el entrenador
        self.__setup_evaluator_and_trainer(user, prefs)

    def __setup_channels_and_head_widget(self, prefs):
        """
        Configura los canales activos basándose en las preferencias y actualiza 
        la representación visual en el widget de la cabeza (ElectrodeHeadWidget).

        Parámetros
        ----------
        prefs : UserPreferences
            Objeto con las preferencias del usuario, incluyendo el mapeo de canales.

        Retorna
        -------
        Ninguno
        """
        channels = prefs.channels
        self.__head_widget.set_locked_channels(builtins.SETTINGS.locked_channels.values())
        self.__ui.load_head_widget(self.__head_widget)
        self.__channels_enable = [channels[ch_idx] for ch_idx in channels]
        self.__head_widget.set_active_channels(self.__channels_enable)
        channels_display = {i + 1: ch for i, ch in enumerate(self.__channels_enable)}
        self.__head_widget.set_channels_display(channels_display)

    def __setup_graphs_and_estimators(self, prefs):
        """
        Configura los estimadores de PSD y los widgets visuales de EEG, 
        cargando los estímulos activos y sus frecuencias objetivo.

        Parámetros
        ----------
        prefs : UserPreferences
            Objeto con las preferencias del usuario, incluyendo ventanas de tiempo y estímulos.

        Retorna
        -------
        Ninguno
        """
        channels = prefs.channels
        self.__eeg_widget.setup_channel_labels(channels)
        self.__eeg_widget.setEnabled(True)
        self.__psd_estimator.load_buffer_size(len(channels))
        self.__psd_estimator.load_calc_interval(prefs.time_window)
        
        # Filtrar estímulos activos
        freqs = [stim.freq for i, stim in enumerate(prefs.stimulus) if prefs.stimulus_on[i]]
        stimulus_use = [stim for i, stim in enumerate(prefs.stimulus) if prefs.stimulus_on[i]]
        self.__stimulus_viewer.load_stimulus_use(stimulus_use)
        self.__psd_widget.set_target_frequencies(freqs)
        self.__psd_widget.setEnabled(True)
        self.__psd_widget.set_psd_mode(2)
        self.__ui.load_psd_graph(self.__psd_widget)
        self.__ui.load_eeg_graph(self.__eeg_widget)

    def __setup_classifier(self, prefs):
        """
        Desconecta el clasificador antiguo de forma segura e inicializa el nuevo 
        (CCA o eTRCA) según el método seleccionado en las preferencias del usuario.

        Parámetros
        ----------
        prefs : UserPreferences
            Objeto con las preferencias del usuario, incluyendo el método de clasificación.

        Retorna
        -------
        Ninguno
        """
        # Desconectar clasificador previo de forma segura
        if self.__signal_classifier is not None:
            try:
                self.__ui.threshold_request.disconnect(self.__signal_classifier.load_threshold)
            except (TypeError, RuntimeError):
                pass
        self.__signal_classifier = None
        
        # Instanciar el nuevo clasificador
        if prefs.classification_method == ClassificationMethods.eTRCA.name:
            self.__signal_classifier = SignalTRCAClassifier()
            self.__signal_classifier.load_filterbank(self.__eeg_filters.filterbank)
            self.__processing_worker.load_classifier(self.__signal_classifier)
        elif prefs.classification_method == ClassificationMethods.CCA.name:
            self.__signal_classifier = SignalCCAClassifier()
            self.__signal_classifier.load_data(
                builtins.SAMPLE_RATE,
                prefs,
                builtins.SETTINGS.f_bands,
            )
            self.__processing_worker.load_classifier(self.__signal_classifier)
            self.__processing_worker.set_classifier_ready(True)
            
        #reconectar señal de umbral si aplica
        if self.__signal_classifier is not None:
            self.__ui.threshold_request.connect(self.__signal_classifier.load_threshold)

    def __setup_evaluator_and_trainer(self, user, prefs):
        """
        Configura los módulos de evaluación BCI (generación de reportes) y el 
        entrenador con los datos y canales del usuario actual.

        Parámetros
        ----------
        user : dict
            Diccionario con la información del usuario (incluye 'name').
        prefs : UserPreferences
            Objeto con las preferencias del usuario para el evaluador.

        Retorna
        -------
        Ninguno
        """
        report_path = f"reporte-bci-{user['name']}.pdf"
        self.__bci_evaluator.set_user_preferences(prefs)
        self.__bci_evaluator.set_report_path(report_path)
        self.__bci_evaluator.set_auto_generate_report(True)
        self.__trainer.load_name(user["name"])
        self.__trainer.load_channels_info(self.__channels_enable)

    def __on_remove_user_request(self, user_id):
        """
        Elimina de la base de datos toda la información asociada a un usuario, incluyendo 
        sus preferencias configuradas, sus matrices de pesos de entrenamiento y su registro 
        de usuario. Limpia el estado actual del controlador.

        Parámetros
        ----------
        user_id : int
            Identificador único del usuario a eliminar.

        Retorna
        -------
        Ninguno
        """
        self.__prefs_manager.remove_user_preferences(user_id)
        self.__weights_manager.remove_user_weights(user_id)
        self.__user_manager.remove_user(user_id)
        self.__ui.load_users(self.__user_manager.get_all_users())
        self.__actual_user = None
        self.__channels_enable = []

    def __on_save_user_preferences_request(self, user_id, prefs):
        """
        Guarda o actualiza las modificaciones hechas a las preferencias de un usuario, 
        asociando la lista actual de canales activos y forzando la recarga del perfil 
        del usuario para aplicar los cambios.

        Parámetros
        ----------
        user_id : int
            Identificador del usuario a quien pertenecen las preferencias.
        prefs : UserPreferences
            Objeto con los nuevos parámetros de configuración modificados desde la UI.

        Retorna
        -------
        Ninguno
        """
        self.__processing_worker.set_classifier_ready(False)
        prefs_save = prefs
        prefs_save.channels = {index: channel for index, channel in enumerate(self.__channels_enable)}
        self.__channels_enable = []
        self.__prefs_manager.save_or_update_preferences(user_id, prefs_save)
        self.__on_request_user_preferences(user_id)

    def __on_start_training_request(self):
        """
        Gestiona la petición de inicio de la etapa de entrenamiento. Si el método es CCA,
        cancela la operación informando al usuario. Si es eTRCA, verifica 
        si ya existen pesos previos para ofrecer su reutilización o arrancar un nuevo proceso.

        Retorna
        -------
        Ninguno
        """
        prefs = self.__prefs_manager.get_user_preferences(self.__actual_user)
        if prefs.classification_method == ClassificationMethods.CCA.name:
            self.__ui.write_box_message("El metodo de deteccion seleccionado no requiere entrenamiento")
            return
        if prefs.classification_method == ClassificationMethods.eTRCA.name:
            old_train = self.__weights_manager.get_user_weights(self.__actual_user)
            response = False
            if old_train is not None:
                if self.__check_shape_compatibility(old_train, prefs):
                    text = "El usuario ya cuenta con un entrenamiento previo. Desea utilizarlos?"
                    response = self.__ui.confirm_action(text, "Si", "No", "Confirmar")
            if not response:
                self.__training_in_progress = True
                self.__ui.enabled_button_filters(False)
                self.__trainer.add_filter_data(self.__eeg_filters.get_parameters())
                if not self.__stimulus_viewer.is_running():
                    self.__stimulus_viewer.start_stim()
                else:
                    self.__start_training()
            else:
                self.__on_training_finish(old_train)
    
    def __check_shape_compatibility(self, p_old_train, p_prefs):
        """
        Verifica la compatibilidad dimensional entre los datos de entrenamiento guardados
        y la configuración actual del sistema.
        
        Comprueba que:
        - El número de muestras en los trains coincida con la ventana de tiempo configurada
        - El número de canales en los pesos (w) coincida con los canales habilitados
        - El número de targets coincida con la cantidad de estímulos activos
        
        Args:
            p_old_train (dict): Diccionario con los datos de entrenamiento previamente guardados.
                Debe contener las claves:
                - 'trains': lista de arrays seniales promediadas
                - 'w': lista de arrays con los pesos espaciales de TRCA
                - 'num_targets': número de stímulos en el entrenamiento
            p_prefs (object): Objeto de UserPreferences con las preferencias del usuario.

        
        Returns:
            bool: True si todas las dimensiones son compatibles, False en caso contrario.
                También retorna False si ocurre alguna excepción durante la verificación.
        
        Note:
            Este método captura cualquier excepción y muestra un mensaje en la UI
            en lugar de propagar el error, para permitir un manejo más amigable.
        """
        is_compatible = True
        
        try:
            if len(p_old_train['trains'][0][0][0]) != int(p_prefs.time_window * builtins.SAMPLE_RATE):
                is_compatible = False
            if len(p_old_train['w'][0][0]) != len(self.__channels_enable):
                is_compatible = False
            if p_old_train['num_targets'] != sum(p_prefs.stimulus_on):
                is_compatible = False
        except Exception as e:
            text = f"No se pudo verificar la compatibilidad del entrenamiento guardado debido a: {e}"
            self.__ui.write_message(text)
            is_compatible = False
        
        return is_compatible

            

    def __start_training(self):
        """
        Lanza de manera efectiva la rutina cronometrada de entrenamiento (dando un margen 
        de 5 segundos de preparación) una vez que los estímulos visuales están listos.

        Retorna
        -------
        Ninguno
        """
        self.__training_in_progress = True
        QTimer.singleShot(5000, self.__trainer.start_training)

    def __on_training_finish(self, p_training_result):
        """
        Slot que se ejecuta al finalizar el entrenamiento. Detiene los estímulos visuales, 
        guarda las matrices/pesos resultantes en la base de datos y carga dichos datos 
        en el clasificador de señales dejandolo listo para la clasificacion.

        Parámetros
        ----------
        p_training_result : any
            Datos o coeficientes resultantes del algoritmo de entrenamiento matemático.

        Retorna
        -------
        Ninguno
        """
        self.__ui.enabled_button_filters(True)
        self.__training_in_progress = False
        self.__stimulus_viewer.stop_stim()
        self.__stimulus_ready = False
        self.__ui.write_message("Entrenamiento finalizado")
        user_prefs = self.__prefs_manager.get_user_preferences(self.__actual_user)
        n_channels = len(user_prefs.channels)
        time_window = user_prefs.time_window
        self.__signal_classifier.load_data(
            builtins.SAMPLE_RATE,
            p_training_result,
            n_channels,
            time_window,
        )
        self.__processing_worker.set_classifier_ready(True)
        self.__weights_manager.save_or_update_weights(self.__actual_user, p_training_result)

    def __on_start_test_request(self):
        """
        Prepara e inicia una prueba de evaluación BCI. Configura y muestra las ventanas de 
        secuencias del operador y del usuario, realiza el mapeo de señales y enciende 
        el estimulador visual.

        Retorna
        -------
        Ninguno
        """
        self.__bci_evaluator_op_interfaz.load_sequence_widget(self.__sequence_widget_op)
        self.__sequence_widget_user.hide_detected_sequence(False)
        self.__bci_evaluator_user_interfaz.load_sequence_widget(self.__sequence_widget_user)
        self.__sequence_widget_op.add_sequence(builtins.SETTINGS.sequence_evaluation)
        self.__sequence_widget_user.add_sequence(builtins.SETTINGS.sequence_evaluation)
        self.__connect_test_signals()
        self.__bci_evaluator_op_interfaz.show()
        self.__bci_evaluator_user_interfaz.show()
        self.__test_is_running = True
        self.__add_test_data = True
        if self.__stimulus_ready:
            self.__start_test()
        else:
            self.__stimulus_viewer.start_stim()

    def __connect_test_signals(self):
        """
        Establece las conexiones de eventos exclusivas para el módulo de evaluación BCI, 
        vinculando los aciertos, fallos y tiempos de respuesta con las interfaces 
        gráficas del test.

        Retorna
        -------
        Ninguno
        """
        self.__disconnect_test_signals()
        self.__bci_evaluator.index_hit.connect(self.__on_index_hit)
        self.__bci_evaluator.index_miss.connect(self.__on_index_miss)
        self.__bci_evaluator.index_sequence.connect(self.__sequence_widget_op.highlight_expected_index)
        self.__bci_evaluator.index_sequence.connect(self.__sequence_widget_user.highlight_expected_index)
        self.__bci_evaluator.finished.connect(self.__on_stop_test_request)
        self.__bci_evaluator.send_time.connect(self.__bci_evaluator_op_interfaz.set_time)
        self.__bci_evaluator_op_interfaz.stop_test_request.connect(self.__on_stop_test_request)
        self.__bci_evaluator_op_interfaz.start_test_request.connect(self.__bci_evaluator.start)
        self.__bci_evaluator_user_interfaz.window_closed.connect(self.__on_stop_test_request)

    def __disconnect_test_signals(self):
        """
        Desconecta de forma segura las señales de control de la prueba de evaluación BCI 
        para evitar fugas de memoria, ejecuciones duplicadas o excepciones de Qt (RuntimeError).

        Retorna
        -------
        Ninguno
        """
        try:
            self.__bci_evaluator.index_hit.disconnect(self.__on_index_hit)
            self.__bci_evaluator.index_miss.disconnect(self.__on_index_miss)
            self.__bci_evaluator.index_sequence.disconnect(self.__sequence_widget_op.highlight_expected_index)
            self.__bci_evaluator.index_sequence.disconnect(self.__sequence_widget_user.highlight_expected_index)
            self.__bci_evaluator.finished.disconnect(self.__on_stop_test_request)
            self.__bci_evaluator_op_interfaz.stop_test_request.disconnect(self.__on_stop_test_request)
            self.__bci_evaluator_user_interfaz.window_closed.disconnect(self.__on_stop_test_request)
            self.__bci_evaluator_op_interfaz.start_test_request.disconnect(self.__bci_evaluator.start)
            self.__bci_evaluator.send_time.disconnect(self.__bci_evaluator_op_interfaz.set_time)
        except (RuntimeError, TypeError):
            pass

    def __on_stop_test_request(self):        
        """
        Slot que maneja la solicitud de detener la prueba de evaluación BCI. 
        Finaliza el test, detiene el evaluador y cierra las interfaces gráficas asociadas.

        Retorna
        -------
        Ninguno
        """
        self.__test_finished()
        self.__bci_evaluator.stop()
        self.__bci_evaluator_op_interfaz.stop()
        self.__bci_evaluator_user_interfaz.stop()

    def __start_test(self):
        """
        Inicia la ejecución de la prueba BCI. Ajusta la ventana del evaluador de usuario 
        dentro del gestor de juegos, habilita los botones del operador y arranca el parpadeo visual.

        Retorna
        -------
        Ninguno
        """
        screen_info = self.__stimulus_viewer.get_screen_info()
        self.__game_manager.load_screen_info(screen_info)
        self.__game_manager.load_hwnd_viewer(self.__stim_hwnd)
        hwnd = int(self.__bci_evaluator_user_interfaz.winId())
        self.__game_manager.place_window(hwnd)
        self.__bci_evaluator_op_interfaz.enable_buttons()
        self.__stimulus_viewer.start_flicker()

    def __test_finished(self):
        """
        Realiza las tareas de limpieza al finalizar una prueba BCI. Resetea las banderas 
        de estado, desconecta las señales del test y detiene el estimulador visual.

        Retorna
        -------
        Ninguno
        """
        self.__test_is_running = False
        self.__add_test_data = False
        self.__disconnect_test_signals()
        self.__stimulus_viewer.stop_stim()
        self.__stimulus_ready = False

    def __on_index_hit(self, index):
        """
        Slot que se ejecuta cuando el evaluador BCI detecta un acierto (hit). 
        Notifica a la interfaz del operador y actualiza los widgets de secuencia.

        Parámetros
        ----------
        index : int
            Índice del estímulo que fue clasificado correctamente.

        Retorna
        -------
        Ninguno
        """
        self.__bci_evaluator_op_interfaz.register_hit()
        self.__sequence_widget_op.add_hit(index)

    def __on_index_miss(self, index):
        """
        Slot que se ejecuta cuando el evaluador BCI detecta un fallo (miss). 
        Notifica a la interfaz del operador y actualiza los widgets de secuencia.

        Parámetros
        ----------
        index : int
            Índice del estímulo que no fue clasificado correctamente.

        Retorna
        -------
        Ninguno
        """
        self.__bci_evaluator_op_interfaz.register_miss()
        self.__sequence_widget_op.add_miss(index)


    def __on_psd_graph_request(self, value):
        """
        Responde a la solicitud de la UI para activar o desactivar el cálculo 
        y graficado de la Densidad Espectral de Potencia (PSD).

        Parámetros
        ----------
        value : bool
            True para activar el cálculo de PSD, False para desactivarlo.

        Retorna
        -------
        Ninguno
        """
        self.__processing_worker.set_psd_enabled(value)

    def __on_start_game_request(self, game: str):
        """
        Gestiona la petición de inicio de un videojuego o aplicación BCI externa. 
        Si el estimulador no está listo, lo inicia; de lo contrario, lanza el juego directamente.

        Parámetros
        ----------
        game : str
            Nombre o identificador del juego/aplicación a iniciar.

        Retorna
        -------
        Ninguno
        """
        self.__game_start_requested = True
        self.__game_request = game
        if self.__stimulus_ready:
            self.start_game()
        else:
            self.__stimulus_viewer.start_stim()

    def __on_stop_game_request(self):
        """
        Gestiona la petición de detener el videojuego o aplicación BCI externa en ejecución.
        Limpia las variables de estado y cierra el juego.

        Retorna
        -------
        Ninguno
        """
        self.__game_request = None
        self.__game_start_requested = False
        self.__stimulus_viewer.stop_stim()
        self.__stimulus_ready = False
        self.__game_manager.close_game()

    def start_game(self):
        """
        Inicia la ejecución del videojuego o aplicación BCI externa seleccionada, una vez 
        que la ventana del estimulador visual se encuentra lista y se ha capturado la 
        información de la pantalla correspondiente.

        Retorna
        -------
        Ninguno
        """
        if self.__game_request == "Estimulador":
            return
        screen_info = self.__stimulus_viewer.get_screen_info()
        self.__game_manager.load_screen_info(screen_info)
        self.__game_manager.open_game(self.__game_request)
        self.__game_start_requested = False
        self.__stimulus_viewer.start_flicker()

    def __on_scale_request(self, value):
        """
        Actualiza el valor de amplitud global y notifica al widget de EEG 
        para que redibuje las señales con la nueva escala.

        Parámetros
        ----------
        value : float
            Nuevo valor de escala/amplitud solicitado por la UI.

        Retorna
        -------
        Ninguno
        """
        builtins.SETTINGS.amplitude_value = value
        self.__eeg_widget.eeg_settings_changed()

    def __on_eeg_speed_changed(self, value):
        """
        Actualiza la velocidad de desplazamiento temporal de las gráficas de EEG 
        y notifica al widget para que refresque la visualización.

        Parámetros
        ----------
        value : float
            Nuevo valor de velocidad (escala de tiempo) solicitado por la UI.

        Retorna
        -------
        Ninguno
        """
        builtins.SETTINGS.speed_scale = value
        self.__eeg_widget.eeg_settings_changed()

    def __on_eeg_amplitude_changed(self, value):
        """
        Actualiza la unidad de amplitud para los estimadores de PSD y los widgets 
        de visualización de EEG/PSD, y fuerza un redibujado de los gráficos.

        Parámetros
        ----------
        value : float
            Nuevo valor de unidad de amplitud solicitado por la UI.

        Retorna
        -------
        Ninguno
        """
        builtins.SETTINGS.amplitude_scale = value
        self.__psd_estimator.load_amplitude_unit(value)
        self.__psd_widget.set_psd_unit(value)
        self.__eeg_widget.eeg_settings_changed()

    def __on_enable_control_changed(self, val):
        """
        Actualiza la bandera interna que indica si el sistema debe enviar 
        los resultados de clasificación al controlador de teclado (teclas virtuales).

        Parámetros
        ----------
        val : bool
            True para habilitar el control por teclado, False para deshabilitarlo.

        Retorna
        -------
        Ninguno
        """
        self.__enable_control = val

    def __on_stimuli_ready(self, hwnd):
        """
        Slot que se ejecuta cuando la ventana del estimulador visual ha sido creada 
        y está lista. Guarda el handle (HWND) y desencadena la acción pendiente 
        (entrenamiento, test o juego) según el estado actual del sistema.

        Parámetros
        ----------
        hwnd : int
            Handle (identificador de ventana) del estimulador visual listo.

        Retorna
        -------
        Ninguno
        """
        self.__stim_hwnd = hwnd
        self.__stimulus_ready = True
        if self.__training_in_progress:
            self.__start_training()
        elif self.__game_start_requested:
            self.__stimulus_viewer.start_flicker()
            self.start_game()
        elif self.__test_is_running:
            self.__start_test()
        else:
            self.__stimulus_viewer.start_flicker()

    def __on_classification_result(self, result):
        """
        Slot que recibe el resultado de la clasificación de la señal EEG. 
        Si el resultado es válido, lo envía al evaluador de test (si está activo) 
        o al controlador de teclado (si el control está habilitado).

        Parámetros
        ----------
        result : int
            Índice del estímulo clasificado. Si es menor a -1, se ignora por ser inválido.

        Retorna
        -------
        Ninguno
        """
        if result < -1:
            return
        if self.__add_test_data:
            self.__bci_evaluator.add_classification(result)
        if self.__enable_control:
            self.__keyboard.stim_received(result)

    def __on_channel_toggled(self, channel_name, active):
        """
        Gestiona la activación o desactivación de un canal EEG desde el widget de electrodos. 
        Valida que no se exceda el límite máximo de canales permitidos y actualiza la lista 
        interna y la visualización.

        Parámetros
        ----------
        channel_name : str
            Nombre del canal que fue toggled (ej. 'O1', 'Pz').
        active : bool
            True si el canal fue activado, False si fue desactivado.

        Retorna
        -------
        Ninguno
        """
        max_channels = builtins.SETTINGS.max_channels
        if active:
            if len(self.__channels_enable) >= max_channels:
                self.__ui.write_box_message(f"No puede seleccionar mas de {max_channels} canales")
                self.__head_widget.set_active_channels(self.__channels_enable)
                return
            if channel_name not in self.__channels_enable:
                self.__channels_enable.append(channel_name)
        else:
            if channel_name in self.__channels_enable:
                self.__channels_enable.remove(channel_name)
        channels_dict = {}
        for index, channel in enumerate(self.__channels_enable):
            channels_dict[index+1] = channel
        self.__head_widget.set_active_channels(self.__channels_enable)
        self.__head_widget.set_channels_display(channels_dict)
        
        