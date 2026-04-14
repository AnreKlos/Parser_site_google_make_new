import sys
import os
import json
import webbrowser
from pathlib import Path
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QHBoxLayout, QLineEdit, QPushButton, QTextEdit,
                               QLabel, QCheckBox, QMessageBox)
from PySide6.QtCore import Qt

from scraper_core import run_parse
import site_generator.generate_site as gen_site


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Парсер сайтов")
        self.setGeometry(100, 100, 800, 600)

        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Main layout
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(15)

        # URL input section
        url_layout = QHBoxLayout()
        url_label = QLabel("Ссылка на сайт дилера:")
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://example.com")
        url_layout.addWidget(url_label)
        url_layout.addWidget(self.url_input)
        main_layout.addLayout(url_layout)

        # LLM checkbox
        self.llm_checkbox = QCheckBox("Принудительно использовать LLM")
        self.llm_checkbox.setChecked(True)
        main_layout.addWidget(self.llm_checkbox)

        # Generate button
        self.generate_btn = QPushButton("Сгенерировать лендинг")
        self.generate_btn.setMinimumHeight(50)
        self.generate_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        main_layout.addWidget(self.generate_btn)

        # Log area
        log_label = QLabel("Лог:")
        main_layout.addWidget(log_label)
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMinimumHeight(300)
        main_layout.addWidget(self.log_area)

        # Connect signals
        self.generate_btn.clicked.connect(self.on_generate)

        # State
        self.current_result = None

    def log(self, message: str):
        self.log_area.append(message)

    def clear_log(self):
        self.log_area.clear()

    def on_generate(self):
        url = self.url_input.text().strip()
        if not url:
            self.log("Ошибка: Введите URL")
            QMessageBox.warning(self, "Ошибка", "Введите URL сайта дилера")
            return

        self.clear_log()
        self.log(f"Запуск обработки: {url}")
        
        try:
            use_llm = self.llm_checkbox.isChecked()
            self.log(f"Использование LLM: {use_llm}")
            
            # Run parser
            self.log("Запуск парсера...")
            result = run_parse(
                url,
                force_llm=use_llm,
                auto_detect_only=False,
                save_json=True,
                output_path=None,
                use_js=False,
            )
            self.current_result = result
            
            self.log(f"Парсинг завершен успешно!")
            self.log(f"Результат сохранен в: {result.get('output_path', 'неизвестно')}")
            self.log(f"Домен: {result.get('domain', 'неизвестно')}")
            self.log(f"Оценка: {result.get('score', 'N/A')}")
            self.log(f"Использован LLM: {result.get('used_llm', False)}")
            
            # Generate landing page
            md_path = result.get('output_path')
            if md_path:
                json_path = str(Path(md_path).with_suffix('.json'))
                self.log(f"DEBUG GUI json_path: {json_path}")
                if os.path.exists(json_path):
                    # Load and log normalized data for debugging
                    try:
                        with open(json_path, 'r', encoding='utf-8') as f:
                            json_data = json.load(f)
                        normalized = json_data.get('normalized', {})
                        dealership_info = normalized.get('dealership_info', {})
                        self.log(f"DEBUG normalized.address: {dealership_info.get('address', 'НЕТ')}")
                        self.log(f"DEBUG normalized.phones: {dealership_info.get('phones', [])}")
                    except Exception as e:
                        self.log(f"Ошибка чтения JSON для отладки: {e}")
                    
                    self.log("Генерация лендинга...")
                    try:
                        output_path = gen_site.build_site(json_path, template_name='auto_dealer')
                        self.log(f"Успех! Сайт сгенерирован: {output_path}")
                        
                        # Open in browser
                        if output_path and os.path.exists(output_path):
                            webbrowser.open_new_tab(output_path)
                            self.log(f"Сайт открыт в браузере: {output_path}")
                    except Exception as e:
                        self.log(f"Ошибка генерации лендинга: {e}")
                        QMessageBox.critical(self, "Ошибка", f"Генерация лендинга не удалась:\n{e}")
                else:
                    self.log("Ошибка: JSON файл не найден")
                    QMessageBox.critical(self, "Ошибка", "JSON файл не найден")
            else:
                self.log("Ошибка: JSON файл не найден")
                QMessageBox.critical(self, "Ошибка", "JSON файл не найден")
                
        except Exception as e:
            self.log(f"Ошибка: {e}")
            QMessageBox.critical(self, "Ошибка", f"Обработка не удалась:\n{e}")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()