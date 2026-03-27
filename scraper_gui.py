import sys
import requests
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QLineEdit, QPushButton, QTextEdit, 
                               QLabel, QTreeWidget, QTreeWidgetItem, QSplitter,
                               QMessageBox, QFileDialog, QTabWidget, QGroupBox,
                               QFormLayout)
from PySide6.QtCore import Qt, Signal
from bs4 import BeautifulSoup
from urllib.parse import urlparse

from scraper_core import run_parse
from auto_detector import auto_detect
from config_manager import load_config, save_config


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Парсер сайтов")
        self.setGeometry(100, 100, 1200, 800)

        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Main layout
        main_layout = QVBoxLayout(central_widget)

        # URL input section
        url_layout = QHBoxLayout()
        url_label = QLabel("URL:")
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://example.com")
        url_layout.addWidget(url_label)
        url_layout.addWidget(self.url_input)

        # Add URL layout to main
        main_layout.addLayout(url_layout)

        # Tab widget
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # Tab 1: Результаты
        self.results_tab = QWidget()
        results_layout = QVBoxLayout(self.results_tab)

        # Buttons for results tab
        results_btn_layout = QHBoxLayout()
        self.auto_detect_btn = QPushButton("Автоопределение")
        self.run_btn = QPushButton("Запустить")
        results_btn_layout.addWidget(self.auto_detect_btn)
        results_btn_layout.addWidget(self.run_btn)
        results_layout.addLayout(results_btn_layout)

        # Tree for results
        self.results_tree = QTreeWidget()
        self.results_tree.setHeaderLabel("Результаты")
        results_layout.addWidget(self.results_tree)

        self.tabs.addTab(self.results_tab, "Результаты")

        # Tab 2: Селекторы
        self.selectors_tab = QWidget()
        selectors_layout = QVBoxLayout(self.selectors_tab)

        # Form for selectors
        form_layout = QFormLayout()

        # Price selectors
        self.price_selectors_edit = QLineEdit()
        self.price_selectors_edit.setPlaceholderText("CSS селекторы, через запятую")
        form_layout.addRow("Цены:", self.price_selectors_edit)

        # Contact selectors group
        contact_group = QGroupBox("Контакты")
        contact_layout = QFormLayout()

        self.phones_edit = QLineEdit()
        self.phones_edit.setPlaceholderText("CSS селекторы, через запятую")
        contact_layout.addRow("Телефоны:", self.phones_edit)

        self.emails_edit = QLineEdit()
        self.emails_edit.setPlaceholderText("CSS селекторы, через запятую")
        contact_layout.addRow("Email:", self.emails_edit)

        self.address_edit = QLineEdit()
        self.address_edit.setPlaceholderText("CSS селекторы, через запятую")
        contact_layout.addRow("Адрес:", self.address_edit)

        self.social_edit = QLineEdit()
        self.social_edit.setPlaceholderText("CSS селекторы, через запятую")
        contact_layout.addRow("Соцсети:", self.social_edit)

        contact_group.setLayout(contact_layout)
        form_layout.addRow(contact_group)

        # Service selectors
        self.service_selectors_edit = QLineEdit()
        self.service_selectors_edit.setPlaceholderText("CSS селекторы, через запятую")
        form_layout.addRow("Услуги:", self.service_selectors_edit)

        selectors_layout.addLayout(form_layout)

        # Save config button
        self.save_config_btn = QPushButton("Сохранить конфиг")
        selectors_layout.addWidget(self.save_config_btn)

        selectors_layout.addStretch()

        self.tabs.addTab(self.selectors_tab, "Селекторы")

        # Tab 3: Лог
        self.log_tab = QWidget()
        log_layout = QVBoxLayout(self.log_tab)
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        log_layout.addWidget(self.log_area)
        self.tabs.addTab(self.log_tab, "Лог")

        # Connect signals
        self.auto_detect_btn.clicked.connect(self.on_auto_detect)
        self.run_btn.clicked.connect(self.on_run_parse)
        self.save_config_btn.clicked.connect(self.on_save_config)

        # State
        self.current_result = None
        self.current_config = None
        self.current_data = None
        self.detection_result = None

    def log(self, message: str):
        self.log_area.append(message)

    def clear_log(self):
        self.log_area.clear()

    def on_auto_detect(self):
        url = self.url_input.text().strip()
        if not url:
            self.log("Ошибка: Введите URL")
            return

        self.clear_log()
        self.log(f"Авто-определение структуры: {url}")
        
        try:
            # Fetch the page
            self.log("Загрузка страницы...")
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            html = response.text
            soup = BeautifulSoup(html, 'html.parser')
            
            # Run auto-detection only (no file saving)
            self.log("Запуск авто-определения...")
            result = run_parse(url, auto_detect_only=True)
            
            self.detection_result = result
            self.current_config = result.get("selectors", {})
            score = result.get("score", 0)
            
            self.log(f"Авто-определение завершено. Оценка: {score:.2f}")
            self.log(f"Обнаруженные селекторы:")
            for key, value in self.current_config.items():
                if isinstance(value, dict):
                    self.log(f"  {key}:")
                    for k, v in value.items():
                        self.log(f"    {k}: {v}")
                else:
                    self.log(f"  {key}: {value}")
            
            # Display in tree
            self.display_detection_results(result)
            
            # Populate selectors tab with detected values
            self.populate_selectors_tab(self.current_config)
            
        except Exception as e:
            self.log(f"Авто-определение не удалось: {e}")
            QMessageBox.critical(self, "Ошибка", f"Авто-определение не удалось:\n{e}")

    def populate_selectors_tab(self, config: dict):
        """Fill selector fields with values from config."""
        selectors = config.get("selectors", {})
        
        # Price selectors
        price_sel = selectors.get("price", [])
        if isinstance(price_sel, list):
            self.price_selectors_edit.setText(", ".join(price_sel))
        else:
            self.price_selectors_edit.setText(str(price_sel))
            
        # Contact selectors
        contact = selectors.get("contact", {})
        self.phones_edit.setText(", ".join(contact.get("phones", [])))
        self.emails_edit.setText(", ".join(contact.get("emails", [])))
        self.address_edit.setText(", ".join(contact.get("address", [])))
        self.social_edit.setText(", ".join(contact.get("social", [])))
        
        # Service selectors
        service_sel = selectors.get("service", [])
        if isinstance(service_sel, list):
            self.service_selectors_edit.setText(", ".join(service_sel))
        else:
            self.service_selectors_edit.setText(str(service_sel))

    def display_detection_results(self, result):
        """Display detection results in the results tree."""
        self.results_tree.clear()
        
        # Score item
        score_item = QTreeWidgetItem(self.results_tree)
        score_item.setText(0, f"Оценка: {result.get('score', 0):.2f}")
        self.results_tree.addTopLevelItem(score_item)
        
        # Selectors
        selectors = result.get("selectors", {})
        for key, value in selectors.items():
            item = QTreeWidgetItem(self.results_tree)
            if isinstance(value, dict):
                item.setText(0, f"{key}:")
                for k, v in value.items():
                    child = QTreeWidgetItem(item)
                    child.setText(0, f"{k}: {v}")
                    item.addChild(child)
            else:
                item.setText(0, f"{key}: {value}")
            self.results_tree.addTopLevelItem(item)
        
        # Data preview
        preview = result.get("data_preview", {})
        if preview:
            preview_item = QTreeWidgetItem(self.results_tree)
            preview_item.setText(0, "Предпросмотр данных")
            self.results_tree.addTopLevelItem(preview_item)
            for key, value in preview.items():
                child = QTreeWidgetItem(preview_item)
                child.setText(0, f"{key}: {value}")
                preview_item.addChild(child)
                
        self.results_tree.expandAll()

    def on_run_parse(self):
        url = self.url_input.text().strip()
        if not url:
            self.log("Ошибка: Введите URL")
            return

        self.clear_log()
        self.log(f"Запуск парсинга: {url}")
        
        # Get selectors from the selectors tab
        config = self.get_selectors_from_fields()
        
        # Ask for output path
        domain = urlparse(url).netloc if url else "custom"
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить результаты", f"{domain}.md", 
            "Markdown файлы (*.md);;Все файлы (*)"
        )
        
        if not file_path:
            self.log("Парсинг отменен")
            return
            
        try:
            result = run_parse(
                url, 
                config=config,
                output_path=file_path,
                save_json=True
            )
            self.current_result = result
            self.current_data = result.get("data", {})
            
            self.log(f"Парсинг завершен успешно!")
            self.log(f"Результат сохранен в: {result.get('output_path', 'неизвестно')}")
            self.log(f"Домен: {result.get('domain', 'неизвестно')}")
            self.log(f"Оценка: {result.get('score', 'N/A')}")
            
            # Display extracted data
            self.display_extracted_data(self.current_data)
            
        except Exception as e:
            self.log(f"Парсинг не удался: {e}")
            QMessageBox.critical(self, "Ошибка", f"Парсинг не удался:\n{e}")

    def get_selectors_from_fields(self) -> dict:
        """Collect selectors from input fields."""
        # Parse comma-separated values into lists
        def parse_list(text: str) -> list:
            items = [item.strip() for item in text.split(",") if item.strip()]
            return items if items else []
            
        config = {
            "selectors": {
                "price": parse_list(self.price_selectors_edit.text()),
                "contact": {
                    "phones": parse_list(self.phones_edit.text()),
                    "emails": parse_list(self.emails_edit.text()),
                    "address": parse_list(self.address_edit.text()),
                    "social": parse_list(self.social_edit.text())
                },
                "service": parse_list(self.service_selectors_edit.text())
            }
        }
        return config

    def display_extracted_data(self, data):
        """Display extracted data in the results tree."""
        self.results_tree.clear()
        
        # Prices
        prices = data.get("prices", [])
        price_item = QTreeWidgetItem(self.results_tree)
        price_item.setText(0, f"Цены ({len(prices)})")
        self.results_tree.addTopLevelItem(price_item)
        for price in prices[:50]:
            child = QTreeWidgetItem(price_item)
            child.setText(0, price)
            price_item.addChild(child)
        if len(prices) > 50:
            more = QTreeWidgetItem(price_item)
            more.setText(0, f"... и еще {len(prices) - 50}")
            price_item.addChild(more)
            
        # Contacts
        contacts = data.get("contacts", {})
        if contacts:
            contact_item = QTreeWidgetItem(self.results_tree)
            contact_item.setText(0, "Контакты")
            self.results_tree.addTopLevelItem(contact_item)
            for ctype, values in contacts.items():
                type_item = QTreeWidgetItem(contact_item)
                type_item.setText(0, f"{ctype.title()} ({len(values)})")
                contact_item.addChild(type_item)
                for val in values[:20]:
                    child = QTreeWidgetItem(type_item)
                    child.setText(0, val)
                    type_item.addChild(child)
                if len(values) > 20:
                    more = QTreeWidgetItem(type_item)
                    more.setText(0, f"... и еще {len(values) - 20}")
                    type_item.addChild(more)
                    
        # Services
        services = data.get("services", [])
        service_item = QTreeWidgetItem(self.results_tree)
        service_item.setText(0, f"Услуги ({len(services)})")
        self.results_tree.addTopLevelItem(service_item)
        for service in services[:50]:
            name = service.get("name", "Без названия")
            desc = service.get("description", "")
            text = f"{name}"
            if desc:
                text += f" - {desc[:50]}..."
            child = QTreeWidgetItem(service_item)
            child.setText(0, text)
            service_item.addChild(child)
        if len(services) > 50:
            more = QTreeWidgetItem(service_item)
            more.setText(0, f"... и еще {len(services) - 50}")
            service_item.addChild(more)
            
        self.results_tree.expandAll()

    def on_save_config(self):
        """Save selectors from the selectors tab to config file."""
        config = self.get_selectors_from_fields()
        url = self.url_input.text().strip()
        
        if not url:
            domain = "custom"
        else:
            try:
                domain = urlparse(url).netloc
            except:
                domain = "custom"
            
        try:
            save_config(domain, config)
            self.log(f"Конфигурация сохранена для домена: {domain}")
            QMessageBox.information(self, "Успех", f"Конфигурация сохранена для {domain}")
        except Exception as e:
            self.log(f"Не удалось сохранить конфиг: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить конфиг:\n{e}")


def main():
    app = QApplication(sys.argv)
    
    # Apply basic styling
    app.setStyle("Fusion")
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()