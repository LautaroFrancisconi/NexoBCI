import sys
import os
from PySide6.QtWidgets import QApplication
from src.app.app_controller import AppController



def get_app_dir(folder_name="NexoBCI_data"):
    if getattr(sys, 'frozen', False):
        # Corriendo como .exe empaquetado
        base_dir = os.path.dirname(sys.executable)
    else:
        # Corriendo como script .py normal
        base_dir = os.path.dirname(os.path.abspath(__file__))

    app_dir = os.path.join(base_dir, folder_name)
    os.makedirs(app_dir, exist_ok=True)
    return app_dir

def main():
    app = QApplication(sys.argv)
    app_dir = get_app_dir()
    controller = AppController(data_dir=app_dir)
    controller.start_app()

    sys.exit(app.exec())

if __name__ == "__main__":
    main() 