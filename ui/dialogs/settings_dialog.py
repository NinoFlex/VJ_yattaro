from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, 
    QWidget, QPushButton, QLabel, QLineEdit, QFormLayout,
    QFileDialog
)
from PySide6.QtCore import Qt

class SettingsDialog(QDialog):
    """
    アプリケーションの詳細設定を行うダイアログ
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("詳細設定")
        self.resize(500, 300)
        
        # メインレイアウト
        self.layout = QVBoxLayout(self)
        
        # タブウィジェット
        self.tabs = QTabWidget()
        self.layout.addWidget(self.tabs)
        
        # 各タブの構築
        self._init_general_tab()
        self._init_rekordbox_tab()
        
        # ボタンエリア
        self._init_button_box()

    def _init_general_tab(self):
        """「全般」タブの構築"""
        tab = QWidget()
        layout = QFormLayout(tab)
        
        # 更新間隔（現在は表示のみ）
        self.interval_edit = QLineEdit("10")
        layout.addRow("更新間隔 (秒):", self.interval_edit)
        
        self.tabs.addTab(tab, "全般")

    def _init_rekordbox_tab(self):
        """「Rekordbox」タブの構築"""
        tab = QWidget()
        layout = QFormLayout(tab)
        
        # データベースパス設定
        self.db_path_edit = QLineEdit()
        self.db_path_edit.setPlaceholderText("master.db のパスを選択してください")
        
        path_layout = QHBoxLayout()
        path_layout.addWidget(self.db_path_edit)
        self.browse_btn = QPushButton("参照...")
        self.browse_btn.setFixedWidth(80)
        self.browse_btn.clicked.connect(self.browse_db)
        path_layout.addWidget(self.browse_btn)
        
        layout.addRow("データベースパス:", path_layout)
        
        self.tabs.addTab(tab, "Rekordbox")

    def _init_button_box(self):
        """下部のボタン（適用・キャンセル）の構築"""
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.apply_button = QPushButton("適用")
        self.apply_button.clicked.connect(self.accept)
        
        self.cancel_button = QPushButton("キャンセル")
        self.cancel_button.clicked.connect(self.reject)
        
        button_layout.addWidget(self.apply_button)
        button_layout.addWidget(self.cancel_button)
        
        self.layout.addLayout(button_layout)

    def browse_db(self):
        """ファイルダイアログを開いて master.db を選択する"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Rekordbox master.db を選択",
            "",
            "SQLite Database (master.db);;All Files (*)"
        )
        if file_path:
            self.db_path_edit.setText(file_path)

    def accept(self):
        """適用ボタンが押された時の処理"""
        db_path = self.db_path_edit.text()
        interval = self.interval_edit.text()
        print(f"Settings: Saving DB Path: {db_path}, Interval: {interval}")
        super().accept()
