import sys
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
from semabridge.cli.ui.main_window import launch_ui, SemaBridgeMainWindow

# Mock launch_ui to auto-close
def test_launch():
    from semabridge.core.settings import get_settings
    from PyQt6.QtGui import QFont
    app = QApplication(sys.argv)
    app_settings = get_settings()
    font_size = app_settings.core.ui_font_size
    from PyQt6.QtCore import QSettings
    settings = QSettings("SemaBridge", "App")
    current_theme = settings.value("theme", "light")
    from semabridge.cli.ui.main_window import COLORS, DARK_COLORS, LIGHT_COLORS, get_stylesheet
    if current_theme == "dark":
        COLORS.clear()
        COLORS.update(DARK_COLORS)
    else:
        COLORS.clear()
        COLORS.update(LIGHT_COLORS)
    app.setStyleSheet(get_stylesheet(font_size=font_size))
    window = SemaBridgeMainWindow(app, settings)
    
    # Test toggle
    window.toggle_theme()
    print("Toggle 1 complete")
    window.toggle_theme()
    print("Toggle 2 complete")
    
    QTimer.singleShot(100, app.quit)
    app.exec()

test_launch()
