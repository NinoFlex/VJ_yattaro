from PySide6.QtGui import QColor, QPalette


THEMES = {
    "light": {
        "window": "#f3f3f3",
        "panel": "#ffffff",
        "panel_alt": "#f8f8f8",
        "titlebar": "#f0f0f0",
        "input": "#ffffff",
        "hover": "#e8e8e8",
        "border": "#cccccc",
        "border_soft": "#dddddd",
        "text": "#333333",
        "muted": "#666666",
        "placeholder": "#8a8a8a",
        "selection": "#bbdefb",
        "selection_soft": "#e3f2fd",
        "selection_text": "#111111",
        "header": "#eeeeee",
        "accent": "#1f6fb2",
        "accent_hover": "#2b7fc3",
        "accent_text": "#ffffff",
        "danger": "#e81123",
        "error": "#b00020",
        "link": "#1976d2",
        "youtube_active": "#d7ebff",
        "youtube_active_text": "#0b3558",
        "youtube_list": "#fafafa",
        "youtube_item": "#ffffff",
        "youtube_item_hover": "#f0f0f0",
        "no_image": "#e6e6e6",
        "no_image_text": "#969696",
    },
    "dark": {
        "window": "#151719",
        "panel": "#202326",
        "panel_alt": "#25282b",
        "titlebar": "#1b1e21",
        "input": "#272a2e",
        "hover": "#30343a",
        "border": "#44484d",
        "border_soft": "#35393d",
        "text": "#e8eaed",
        "muted": "#aeb4bb",
        "placeholder": "#80868b",
        "selection": "#214d73",
        "selection_soft": "#193a58",
        "selection_text": "#ffffff",
        "header": "#2a2e32",
        "accent": "#1769aa",
        "accent_hover": "#237abf",
        "accent_text": "#ffffff",
        "danger": "#e81123",
        "error": "#ff7b86",
        "link": "#70b7ff",
        "youtube_active": "#173c5f",
        "youtube_active_text": "#d9edff",
        "youtube_list": "#181b1e",
        "youtube_item": "#202326",
        "youtube_item_hover": "#2a2e32",
        "no_image": "#2c3034",
        "no_image_text": "#9aa0a6",
    },
}


def normalize_theme(theme):
    return "dark" if str(theme or "").strip().lower() == "dark" else "light"


def colors(theme):
    return THEMES[normalize_theme(theme)]


def application_stylesheet(theme):
    c = colors(theme)
    return f"""
        QDialog, QMessageBox, QFileDialog {{
            background-color: {c['window']};
            color: {c['text']};
        }}
        QLabel, QCheckBox, QRadioButton, QGroupBox {{
            color: {c['text']};
        }}
        QGroupBox {{
            border: 1px solid {c['border']};
            border-radius: 5px;
            margin-top: 8px;
            padding-top: 8px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 8px;
            padding: 0 4px;
        }}
        QPushButton {{
            background-color: {c['input']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 4px;
            padding: 5px 10px;
        }}
        QPushButton:hover {{
            background-color: {c['hover']};
        }}
        QPushButton:pressed {{
            background-color: {c['selection_soft']};
        }}
        QPushButton:disabled {{
            color: {c['placeholder']};
            background-color: {c['panel_alt']};
        }}
        QLineEdit, QComboBox, QPlainTextEdit, QTextEdit {{
            background-color: {c['input']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 4px;
            selection-background-color: {c['selection']};
            selection-color: {c['selection_text']};
            padding: 4px;
        }}
        QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
            border: 1px solid {c['accent']};
        }}


        /*
         * QSpinBox は意図的にQSSで装飾しない。
         * WindowsではQSpinBoxのsubcontrolをQSSで触ると、上下ボタンは表示されても
         * ネイティブ矢印が消えることがある。配色はQPaletteに任せることで、
         * ダークモードを維持しつつ標準の▲/▼描画を使用する。
         */

        QComboBox QAbstractItemView {{
            background-color: {c['panel']};
            color: {c['text']};
            border: 1px solid {c['border']};
            selection-background-color: {c['selection']};
            selection-color: {c['selection_text']};
        }}
        QTabWidget::pane {{
            background-color: {c['panel']};
            border: 1px solid {c['border']};
        }}
        QTabBar::tab {{
            background-color: {c['panel_alt']};
            color: {c['muted']};
            border: 1px solid {c['border']};
            border-bottom: none;
            padding: 6px 12px;
        }}
        QTabBar::tab:selected {{
            background-color: {c['panel']};
            color: {c['text']};
        }}
        QTableView, QTableWidget {{
            background-color: {c['panel']};
            alternate-background-color: {c['panel_alt']};
            color: {c['text']};
            gridline-color: {c['border_soft']};
            border: 1px solid {c['border']};
            selection-background-color: {c['selection']};
            selection-color: {c['selection_text']};
        }}
        QHeaderView::section {{
            background-color: {c['header']};
            color: {c['text']};
            border: none;
            border-right: 1px solid {c['border']};
            border-bottom: 1px solid {c['border']};
            padding: 5px;
        }}
        QScrollBar:horizontal, QScrollBar:vertical {{
            background-color: {c['panel_alt']};
            border: none;
        }}
        QScrollBar::handle:horizontal, QScrollBar::handle:vertical {{
            background-color: {c['border']};
            border-radius: 4px;
            min-width: 24px;
            min-height: 24px;
        }}
        QScrollBar::handle:horizontal:hover, QScrollBar::handle:vertical:hover {{
            background-color: {c['muted']};
        }}
        QScrollBar::add-line, QScrollBar::sub-line {{
            width: 0px;
            height: 0px;
        }}
        QLabel[role="muted"] {{ color: {c['muted']}; }}
        QLabel[role="link"] {{ color: {c['link']}; }}
        QToolTip {{
            background-color: {c['panel']};
            color: {c['text']};
            border: 1px solid {c['border']};
        }}
    """


def apply_application_theme(app, theme):
    theme = normalize_theme(theme)
    c = colors(theme)

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(c["window"]))
    palette.setColor(QPalette.WindowText, QColor(c["text"]))
    palette.setColor(QPalette.Base, QColor(c["panel"]))
    palette.setColor(QPalette.AlternateBase, QColor(c["panel_alt"]))
    palette.setColor(QPalette.Text, QColor(c["text"]))
    palette.setColor(QPalette.Button, QColor(c["input"]))
    palette.setColor(QPalette.ButtonText, QColor(c["text"]))
    palette.setColor(QPalette.Highlight, QColor(c["selection"]))
    palette.setColor(QPalette.HighlightedText, QColor(c["selection_text"]))
    palette.setColor(QPalette.ToolTipBase, QColor(c["panel"]))
    palette.setColor(QPalette.ToolTipText, QColor(c["text"]))
    palette.setColor(QPalette.PlaceholderText, QColor(c["placeholder"]))
    app.setPalette(palette)
    app.setStyleSheet(application_stylesheet(theme))
    return theme
