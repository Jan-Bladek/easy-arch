from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from arch_gui_installer.core.config import Config, Partition, Secrets, ValidationError
from arch_gui_installer.hardware.detect import disks, hardware, read_command
from arch_gui_installer.packages.catalog import SOFTWARE, Catalog
from arch_gui_installer.storage.plan import BIOS, ESP, Plan
from arch_gui_installer.storage.plan import layout as disk_layout

PL = {
    "Welcome": "Witaj",
    "Language & keyboard": "Język i klawiatura",
    "Network": "Sieć",
    "Storage": "Dyski i partycje",
    "System & encryption": "System i szyfrowanie",
    "Desktop & drivers": "Pulpit i sterowniki",
    "Packages": "Pakiety",
    "Account & location": "Konto i lokalizacja",
    "Services": "Usługi",
    "Review": "Podsumowanie",
    "Installation": "Instalacja",
    "Back": "Wstecz",
    "Next": "Dalej",
    "Cancel": "Anuluj",
    "Install": "Zainstaluj",
    "Preview only — no disk writes": "Tryb podglądu — bez zapisu na dyskach",
    "Dark theme": "Ciemny motyw",
    "Refresh": "Odśwież",
    "Automatic — erase whole disk": "Automatycznie — wymaż cały dysk",
    "Manual — preserve selected partitions": "Ręcznie — zachowaj wybrane partycje",
    "Add": "Dodaj",
    "Edit": "Edytuj",
    "Filesystem": "System plików",
    "Mountpoint": "Punkt montowania",
    "Action": "Operacja",
    "Role": "Rola",
    "Number": "Numer",
    "Start (MiB)": "Początek (MiB)",
    "Size (MiB)": "Rozmiar (MiB)",
    "Preserve": "Zachowaj",
    "Format": "Formatuj",
    "Username": "Użytkownik",
    "Password": "Hasło",
    "Repeat password": "Powtórz hasło",
    "Hostname": "Nazwa komputera",
    "Timezone": "Strefa czasowa",
    "Locale": "Locale",
    "Lock root account": "Zablokuj konto root",
    "Root password": "Hasło root",
    "Repeat root password": "Powtórz hasło root",
    "Allow sudo": "Uprawnienia sudo",
    "Encrypt root (LUKS2)": "Szyfruj system (LUKS2)",
    "Encryption password": "Hasło szyfrowania",
    "Repeat encryption password": "Powtórz hasło szyfrowania",
    "Bootloader": "Bootloader",
    "Disk swap (MiB)": "Swap na dysku (MiB)",
    "Btrfs snapshots": "Snapshoty Btrfs",
    "Bluetooth": "Bluetooth",
    "Firewall": "Zapora sieciowa",
    "Enable SSH": "Włącz SSH",
    "NTP time synchronization": "Synchronizacja czasu NTP",
    "SSD TRIM timer": "Cykliczny TRIM SSD",
    "Copy live NetworkManager connections (includes Wi-Fi credentials)": "Skopiuj połączenia NetworkManager z live (zawierają hasła Wi-Fi)",
    "Save plan": "Zapisz plan",
    "Save log": "Zapisz log",
    "Reboot": "Uruchom ponownie",
    "Open connection editor": "Otwórz edytor połączeń",
    "Search name or description": "Szukaj nazwy lub opisu",
    "Add package/group": "Dodaj pakiet/grupę",
    "Remove selected": "Usuń zaznaczone",
    "Install Arch Linux with an explicit, reviewable plan.": "Zainstaluj Arch Linux według jawnego planu operacji.",
    "Disk operations cannot be rolled back. Back up your data before installing.": "Operacji dyskowych nie można cofnąć. Przed instalacją wykonaj kopię danych.",
    "Package": "Pakiet",
    "Version": "Wersja",
    "Description": "Opis",
    "Repository": "Repozytorium",
    "All groups": "Wszystkie grupy",
    "Selected packages": "Wybrane pakiety",
    "Validation failed": "Błąd walidacji",
}


class DiskMap(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.capacity = 1
        self.partitions = []
        self.setMinimumHeight(70)

    def set_layout(self, capacity, partitions):
        self.capacity = max(1, capacity // 1024**2)
        self.partitions = partitions
        self.setToolTip(
            "\n".join(
                f"#{p.number}: {p.start_mib} + {p.size_mib} MiB • {p.filesystem} • {p.action}"
                for p in partitions
            )
        )
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(0, 8, self.width(), 45, QColor("#344156"))
        for p in self.partitions:
            x = int(p.start_mib / self.capacity * self.width())
            width = max(2, int(p.size_mib / self.capacity * self.width()))
            color = (
                "#dc635e"
                if p.action == "delete"
                else "#1793d1"
                if p.action == "preserve"
                else "#229b83"
            )
            painter.fillRect(x, 8, width - 1, 45, QColor(color))
            if width > 65:
                painter.setPen(QColor("white"))
                painter.drawText(x + 6, 35, f"#{p.number} {p.filesystem}")
        painter.end()


class Task(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception as exc:
            self.failed.emit(str(exc))


class Wizard(QMainWindow):
    def __init__(self, dry_run=False):
        super().__init__()
        self.lang = "pl"
        self.dry_run = dry_run
        self.tasks = []
        self.detected = []
        self.hw = {}
        self.catalog = None
        self.parts = []
        self.plan: Plan | None = None
        self.proc: QProcess | None = None
        self.buffer = b""
        self.temp = None
        self.succeeded = False
        QApplication.instance().aboutToQuit.connect(self.shutdown_tasks)
        self.setWindowTitle("Easy Arch • Arch Linux Installer")
        self.resize(1180, 800)
        self.setMinimumSize(760, 540)
        self.root = QWidget()
        self.setCentralWidget(self.root)
        outer = QVBoxLayout(self.root)
        top = QHBoxLayout()
        brand = QLabel("▲  EASY ARCH")
        brand.setStyleSheet("font-size: 25px; font-weight: 700; color: #1793d1")
        top.addWidget(brand)
        top.addStretch()
        self.theme = QCheckBox(self.tr("Dark theme"))
        self.theme.setChecked(True)
        self.theme.toggled.connect(self.set_theme)
        top.addWidget(self.theme)
        outer.addLayout(top)
        self.banner = QLabel(
            self.tr("Preview only — no disk writes")
            if dry_run
            else "Arch Linux • x86_64 • Live Installer"
        )
        outer.addWidget(self.banner)
        split = QSplitter()
        self.steps = QListWidget()
        self.steps.setMaximumWidth(260)
        self.steps.setSpacing(3)
        self.steps.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stack = QStackedWidget()
        split.addWidget(self.steps)
        split.addWidget(self.stack)
        outer.addWidget(split, 1)
        self.names = [
            "Welcome",
            "Language & keyboard",
            "Network",
            "Storage",
            "System & encryption",
            "Desktop & drivers",
            "Packages",
            "Account & location",
            "Services",
            "Review",
            "Installation",
        ]
        self.factories = [
            self.welcome,
            self.language_page,
            self.network_page,
            self.storage_page,
            self.system_page,
            self.desktop_page,
            self.packages_page,
            self.account_page,
            self.services_page,
            self.review_page,
            self.install_page,
        ]
        for name, factory in zip(self.names, self.factories):
            self.steps.addItem(self.tr(name))
            page = QWidget()
            layout = QVBoxLayout(page)
            heading = QLabel(self.tr(name))
            heading.setStyleSheet(
                "font-size: 27px; font-weight: 600; padding-bottom: 16px"
            )
            layout.addWidget(heading)
            factory(layout)
            layout.addStretch()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.stack.addWidget(scroll)
        nav = QHBoxLayout()
        self.status = QLabel("")
        self.status.setWordWrap(True)
        nav.addWidget(self.status, 1)
        self.cancel = QPushButton(self.tr("Cancel"))
        self.back = QPushButton(self.tr("Back"))
        self.next = QPushButton(self.tr("Next"))
        self.next.setObjectName("primary")
        self.back.clicked.connect(lambda: self.goto(self.stack.currentIndex() - 1))
        self.next.clicked.connect(self.advance)
        self.cancel.clicked.connect(self.cancel_install)
        for button in [self.cancel, self.back, self.next]:
            nav.addWidget(button)
        outer.addLayout(nav)
        self.set_theme(True)
        self.goto(0)
        QTimer.singleShot(0, self.scan)

    def shutdown_tasks(self):
        for worker in list(self.tasks):
            worker.wait()

    def tr(self, text):
        return PL.get(text, text) if self.lang == "pl" else text

    def task(self, fn, done):
        worker = Task(fn, self)
        self.tasks.append(worker)
        worker.done.connect(done)
        worker.failed.connect(self.task_error)
        worker.finished.connect(lambda: self.tasks.remove(worker))
        worker.start()

    def task_error(self, message):
        self.next.setEnabled(self.stack.currentIndex() < 10)
        self.error(message)

    def error(self, message):
        self.status.setText(message[:240])
        QMessageBox.critical(self, self.tr("Validation failed"), message)

    def set_theme(self, dark):
        bg, panel, fg, border = (
            ("#101722", "#192332", "#edf3ff", "#344156")
            if dark
            else ("#eff4fa", "#ffffff", "#152235", "#b7c8d8")
        )
        self.setStyleSheet(
            f"QWidget {{ background: {bg}; color: {fg}; font-size: 14px; }} QLineEdit,QComboBox,QSpinBox,QPlainTextEdit,QTableWidget,QListWidget {{ background: {panel}; border: 1px solid {border}; border-radius: 5px; padding: 7px; }} QPushButton {{ background: {panel}; border: 1px solid {border}; padding: 9px 20px; border-radius: 6px; }} QPushButton#primary {{ background: #147eb3; color: white; border: none; }} QPushButton:disabled {{ color: #748193; }} QListWidget::item {{ padding: 9px 4px; }} QListWidget::item:selected {{ background: #147eb3; color: white; }} QHeaderView::section {{ background: {panel}; padding: 7px; }} QProgressBar::chunk {{ background: #1793d1; }} QCheckBox {{ padding: 5px; }}"
        )

    def label(self, layout, text):
        label = QLabel(self.tr(text))
        label.setWordWrap(True)
        layout.addWidget(label)
        return label

    def combo(self, items):
        box = QComboBox()
        for label, data in items:
            box.addItem(self.tr(label), data)
        return box

    def entry(self, password=False, value=""):
        entry = QLineEdit(value)
        if password:
            entry.setEchoMode(QLineEdit.EchoMode.Password)
            entry.setMaxLength(1024)
        return entry

    def row(self, form, text, widget):
        form.addRow(self.tr(text), widget)
        return widget

    def check(self, layout, text, checked=True, tip=""):
        box = QCheckBox(self.tr(text))
        box.setChecked(checked)
        if tip:
            box.setToolTip(tip)
        layout.addWidget(box)
        return box

    def welcome(self, layout):
        self.label(layout, "Install Arch Linux with an explicit, reviewable plan.")
        self.label(
            layout,
            "Disk operations cannot be rolled back. Back up your data before installing.",
        )
        self.hw_label = QLabel("…")
        self.hw_label.setWordWrap(True)
        layout.addWidget(self.hw_label)

    def language_page(self, layout):
        form = QFormLayout()
        self.language = self.combo([("Polski", "pl"), ("English", "en")])
        self.keyboard = self.combo(
            [(x, x) for x in ["pl", "us", "uk", "de", "fr", "es"]]
        )
        form.addRow("Language / Język", self.language)
        form.addRow("Keyboard / Klawiatura", self.keyboard)
        layout.addLayout(form)
        self.label(
            layout,
            "Language selection updates the interface. The installed system locale is selected on the account page. / Język systemu wybierzesz na ekranie konta.",
        )
        self.language.currentIndexChanged.connect(self.translate_ui)

    def translate_ui(self):
        previous = self.lang
        self.lang = self.language.currentData()
        if previous == self.lang:
            return
        reverse = {v: k for k, v in PL.items()}
        for widget in self.findChildren(QWidget):
            if isinstance(widget, (QLabel, QPushButton, QCheckBox)):
                current = widget.text()
                key = reverse.get(current, current)
                widget.setText(self.tr(key))
            elif isinstance(widget, QComboBox) and widget not in [self.language]:
                for i in range(widget.count()):
                    key = reverse.get(widget.itemText(i), widget.itemText(i))
                    widget.setItemText(i, self.tr(key))
        for i, name in enumerate(self.names):
            self.steps.item(i).setText(self.tr(name))
        self.goto(self.stack.currentIndex())

    def network_page(self, layout):
        self.network_status = self.label(layout, "…")
        button = QPushButton(self.tr("Refresh"))
        button.clicked.connect(self.network_refresh)
        layout.addWidget(button)
        editor = QPushButton(self.tr("Open connection editor"))
        editor.setEnabled(not self.dry_run)
        editor.setToolTip(
            "Network changes are disabled in dry-run / Zmiany sieci wyłączone w podglądzie"
            if self.dry_run
            else "NetworkManager"
        )
        editor.clicked.connect(
            lambda: QProcess.startDetached("nm-connection-editor", [])
        )
        layout.addWidget(editor)
        self.label(
            layout,
            "NetworkManager: use the connection editor to configure Wi-Fi, Ethernet or VPN. / Skonfiguruj Wi-Fi, Ethernet lub VPN w edytorze połączeń.",
        )

    def network_refresh(self):
        self.task(
            lambda: read_command(
                ["nmcli", "-t", "-f", "STATE,CONNECTIVITY", "general"]
            ),
            lambda text: self.network_status.setText(text),
        )

    def storage_page(self, layout):
        self.disk_box = QComboBox()
        self.disk_box.currentIndexChanged.connect(self.disk_changed)
        layout.addWidget(self.disk_box)
        self.disk_id = self.label(layout, "…")
        self.mode = self.combo(
            [
                ("Automatic — erase whole disk", "auto"),
                ("Manual — preserve selected partitions", "manual"),
            ]
        )
        layout.addWidget(self.mode)
        self.mode.currentIndexChanged.connect(self.partition_table)
        self.disk_map = DiskMap()
        layout.addWidget(self.disk_map)
        self.partition_view = QTableWidget(0, 7)
        self.partition_view.setHorizontalHeaderLabels(
            ["#", "MiB", "+ MiB", "FS", "Mount", "Action", "PARTUUID"]
        )
        self.partition_view.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.partition_view.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.partition_view.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.partition_view.setMinimumHeight(240)
        layout.addWidget(self.partition_view)
        buttons = QHBoxLayout()
        for label, fn in [
            ("Refresh", self.scan),
            ("Add", lambda: self.edit_partition(True)),
            ("Edit", lambda: self.edit_partition(False)),
        ]:
            button = QPushButton(self.tr(label))
            button.clicked.connect(fn)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.label(
            layout,
            "Manual mode supports GPT, creating/deleting partitions in free space and preserving existing filesystems. Resizing and existing encrypted volumes are blocked. / Tryb ręczny obsługuje GPT i wolne miejsce; zmiana rozmiaru i istniejące LUKS są zablokowane.",
        )

    def scan(self):
        def detect():
            return disks(), hardware(), Catalog()

        def ready(value):
            self.detected, self.hw, self.catalog = value
            self.disk_box.blockSignals(True)
            self.disk_box.clear()
            for disk in self.detected:
                d = disk.identity
                self.disk_box.addItem(
                    f"{d.path} • {d.model} • {d.size / 1024**3:.1f} GiB"
                    + (" [IN USE / W UŻYCIU]" if disk.mounted else ""),
                    disk,
                )
            self.disk_box.setCurrentIndex(-1)
            self.disk_box.blockSignals(False)
            self.parts = []
            self.partition_table()
            self.hw_label.setText(
                "<h3>"
                + ("Twój komputer" if self.lang == "pl" else "Your computer")
                + "</h3>"
                + f"<p><b>Firmware</b> &nbsp; {self.hw['firmware'].upper()}<br><b>CPU</b> &nbsp; {self.hw['cpu'].upper()}<br>"
                + "<b>GPU</b> &nbsp; "
                + "<br>".join(self.hw["gpus"])
                + "</p>"
                + (
                    "<p>Wybierz język, skonfiguruj system i sprawdź plan przed instalacją.</p>"
                    if self.lang == "pl"
                    else "<p>Choose your language, configure your system and review the plan before installing.</p>"
                )
            )
            self.boot.setCurrentIndex(0 if self.hw["firmware"] == "uefi" else 1)
            self.group_box.clear()
            self.group_box.addItem(self.tr("All groups"), "")
            for group in sorted(self.catalog.groups):
                self.group_box.addItem(group, group)
            self.search_packages()
            self.status.setText(
                f"{len(self.detected)} disks • {len(self.catalog.packages)} packages"
            )

        self.task(detect, ready)

    def disk_changed(self):
        disk = self.disk_box.currentData()
        self.parts = []
        if disk:
            self.disk_id.setText(
                json.dumps(asdict(disk.identity), indent=2)
                + ("\nIN USE — installation blocked" if disk.mounted else "")
            )
            for node in disk.partitions:
                if node.get("type") != "part":
                    continue
                kind = (node.get("parttype") or "").lower()
                fs = node.get("fstype") or ("bios" if kind == BIOS else "unknown")
                self.parts.append(
                    Partition(
                        int(node["partn"]),
                        int(node["start"]) * 512 // 1024**2,
                        int(node["size"]) // 1024**2,
                        fs,
                        "",
                        "preserve",
                        node.get("partuuid") or "",
                        "esp" if kind == ESP else "bios" if kind == BIOS else "data",
                    )
                )
        self.partition_table()

    def partition_table(self):
        if not hasattr(self, "partition_view"):
            return
        disk = self.disk_box.currentData()
        visible_parts = self.parts
        if disk and self.mode.currentData() == "auto" and hasattr(self, "fs"):
            try:
                visible_parts = disk_layout(self.config())
            except Exception:
                pass
        self.disk_map.set_layout(disk.identity.size if disk else 1, visible_parts)
        self.partition_view.setRowCount(len(self.parts))
        for row, p in enumerate(self.parts):
            for col, value in enumerate(
                [
                    p.number,
                    p.start_mib,
                    p.size_mib,
                    p.filesystem,
                    p.mountpoint,
                    p.action,
                    p.partuuid,
                ]
            ):
                self.partition_view.setItem(row, col, QTableWidgetItem(str(value)))

    def edit_partition(self, new=False):
        disk = self.disk_box.currentData()
        if not disk:
            return
        row = self.partition_view.currentRow()
        if not new and row < 0:
            return
        p = (
            Partition(
                max([p.number for p in self.parts] + [0]) + 1, 1, 1024, action="create"
            )
            if new
            else self.parts[row]
        )
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Edit"))
        form = QFormLayout(dialog)
        number, start, size = QSpinBox(), QSpinBox(), QSpinBox()
        for widget, val in [
            (number, p.number),
            (start, p.start_mib),
            (size, p.size_mib),
        ]:
            widget.setRange(1, 2**31 - 1)
            widget.setValue(val)
            widget.setEnabled(new or p.action == "create")
        fs = self.combo(
            [
                (s, s)
                for s in ["ext4", "btrfs", "xfs", "vfat", "bios", "ntfs", "unknown"]
            ]
        )
        fs.setCurrentText(p.filesystem)
        mount = self.combo(
            [
                (s or "—", s)
                for s in ["", "/", "/boot", "/home", "/var/log", "/srv", "/opt"]
            ]
        )
        mount.setCurrentIndex(max(0, mount.findData(p.mountpoint)))
        action = self.combo(
            [
                (s, s)
                for s in (
                    ["create"]
                    if new or p.action == "create"
                    else ["preserve", "format", "delete"]
                )
            ]
        )
        action.setCurrentIndex(max(0, action.findData(p.action)))
        role = self.combo([(s, s) for s in ["data", "esp", "bios"]])
        role.setCurrentIndex(role.findData(p.role))
        for label, widget in [
            ("Number", number),
            ("Start (MiB)", start),
            ("Size (MiB)", size),
            ("Filesystem", fs),
            ("Mountpoint", mount),
            ("Action", action),
            ("Role", role),
        ]:
            self.row(form, label, widget)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            edited = Partition(
                number.value(),
                start.value(),
                size.value(),
                fs.currentData(),
                mount.currentData(),
                action.currentData(),
                p.partuuid,
                role.currentData(),
            )
            if new:
                self.parts.append(edited)
            else:
                self.parts[row] = edited
            self.mode.setCurrentIndex(1)
            self.partition_table()

    def system_page(self, layout):
        form = QFormLayout()
        self.fs = self.combo([(s, s) for s in ["ext4", "btrfs", "xfs"]])
        self.boot = self.combo([("systemd-boot", "systemd-boot"), ("GRUB", "grub")])
        self.swap = QSpinBox()
        self.swap.setRange(0, 262144)
        self.fs.currentIndexChanged.connect(self.partition_table)
        self.swap.valueChanged.connect(self.partition_table)
        for label, widget in [
            ("Filesystem", self.fs),
            ("Bootloader", self.boot),
            ("Disk swap (MiB)", self.swap),
        ]:
            self.row(form, label, widget)
        layout.addLayout(form)
        self.kernels = {}
        for kernel in ["linux", "linux-lts", "linux-zen", "linux-hardened"]:
            self.kernels[kernel] = self.check(layout, kernel, kernel == "linux")
        self.zram = self.check(layout, "zram", True)
        self.snapshots = self.check(layout, "Btrfs snapshots", False)
        self.encrypt = self.check(
            layout,
            "Encrypt root (LUKS2)",
            False,
            "Automatic mode only; /boot is unencrypted. TPM2 is not supported.",
        )
        form = QFormLayout()
        self.luks_pass = self.row(form, "Encryption password", self.entry(True))
        self.luks_repeat = self.row(
            form, "Repeat encryption password", self.entry(True)
        )
        self.pbkdf = self.row(
            form, "PBKDF", self.combo([("Argon2id", "argon2id"), ("PBKDF2", "pbkdf2")])
        )
        layout.addLayout(form)

    def desktop_page(self, layout):
        self.desktop = self.combo(
            [
                ("KDE Plasma • Wayland", "kde"),
                ("Hyprland 0.56 + Noctalia 5", "hyprland"),
            ]
        )
        layout.addWidget(self.desktop)
        form = QFormLayout()
        self.gpu = self.row(
            form,
            "GPU",
            self.combo(
                [
                    ("Auto • Mesa / Nouveau", "auto"),
                    ("Mesa", "mesa"),
                    ("NVIDIA open DKMS • Turing+ only", "nvidia-open"),
                    ("Nouveau", "nouveau"),
                ]
            ),
        )
        self.cpu = self.row(
            form,
            "CPU microcode",
            self.combo(
                [("Auto", "auto"), ("Intel", "intel"), ("AMD", "amd"), ("None", "none")]
            ),
        )
        layout.addLayout(form)
        self.label(
            layout,
            "NVIDIA: verify your GPU generation before selecting open DKMS. Older hardware uses Nouveau; legacy proprietary drivers are not included. / Sprawdź generację karty przed wyborem otwartego modułu NVIDIA.",
        )

    def packages_page(self, layout):
        self.search = self.entry()
        self.search.setPlaceholderText(self.tr("Search name or description"))
        self.search.textChanged.connect(self.search_packages)
        layout.addWidget(self.search)
        self.group_box = QComboBox()
        self.group_box.currentIndexChanged.connect(self.search_packages)
        layout.addWidget(self.group_box)
        self.package_table = QTableWidget(0, 4)
        self.package_table.setHorizontalHeaderLabels(
            [self.tr(s) for s in ["Package", "Version", "Description", "Repository"]]
        )
        self.package_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.package_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.package_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.package_table.setMinimumHeight(220)
        self.package_table.itemDoubleClicked.connect(self.add_from_table)
        layout.addWidget(self.package_table)
        buttons = QHBoxLayout()
        add = QPushButton(self.tr("Add package/group"))
        add.clicked.connect(self.add_package)
        buttons.addWidget(add)
        select = QPushButton(self.tr("Add"))
        select.clicked.connect(self.add_from_table)
        buttons.addWidget(select)
        remove = QPushButton(self.tr("Remove selected"))
        remove.clicked.connect(
            lambda: [
                self.selected.takeItem(self.selected.row(i))
                for i in self.selected.selectedItems()
            ]
        )
        buttons.addWidget(remove)
        layout.addLayout(buttons)
        self.label(layout, "Selected packages")
        self.selected = QListWidget()
        self.selected.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.selected.setMaximumHeight(100)
        layout.addWidget(self.selected)
        self.software = {}
        row = QHBoxLayout()
        for name in SOFTWARE:
            box = QCheckBox(name.capitalize())
            self.software[name] = box
            row.addWidget(box)
        layout.addLayout(row)

    def search_packages(self, *_):
        if not self.catalog:
            return
        result = self.catalog.search(
            self.search.text(), self.group_box.currentData() or ""
        )[:500]
        self.package_table.setRowCount(len(result))
        for row, p in enumerate(result):
            for col, value in enumerate(
                [p.name, p.version, p.description, p.repository]
            ):
                self.package_table.setItem(row, col, QTableWidgetItem(value))

    def add_from_table(self, *_):
        for row in sorted({i.row() for i in self.package_table.selectedItems()}):
            self.add_name(self.package_table.item(row, 0).text())

    def add_name(self, name):
        if self.catalog:
            try:
                self.catalog.expand([name])
            except ValueError as exc:
                self.error(str(exc))
                return
        if not self.selected.findItems(name, Qt.MatchFlag.MatchExactly):
            self.selected.addItem(name)

    def add_package(self):
        name, ok = QInputDialog.getText(
            self, self.tr("Add package/group"), "Name / Nazwa"
        )
        if ok:
            for item in name.split():
                self.add_name(item)

    def account_page(self, layout):
        form = QFormLayout()
        self.username = self.row(form, "Username", self.entry(value="arch"))
        self.user_groups = self.row(form, "Groups / Grupy", self.entry())
        self.user_groups.setToolTip(
            "audio, video, input, storage, optical, libvirt, lp, scanner, uucp"
        )
        self.password = self.row(form, "Password", self.entry(True))
        self.password_repeat = self.row(form, "Repeat password", self.entry(True))
        self.root_password = self.row(form, "Root password", self.entry(True))
        self.root_repeat = self.row(form, "Repeat root password", self.entry(True))
        self.hostname = self.row(form, "Hostname", self.entry(value="easy-arch"))
        self.timezone = self.row(form, "Timezone", self.entry(value="Europe/Warsaw"))
        self.locale = self.row(
            form,
            "Locale",
            self.combo(
                [
                    (s, s)
                    for s in [
                        "pl_PL.UTF-8",
                        "en_US.UTF-8",
                        "en_GB.UTF-8",
                        "de_DE.UTF-8",
                    ]
                ]
            ),
        )
        layout.addLayout(form)
        self.root_locked = self.check(layout, "Lock root account", True)
        self.sudo = self.check(layout, "Allow sudo", True)

    def services_page(self, layout):
        self.bluetooth = self.check(layout, "Bluetooth", True)
        self.firewall = self.check(layout, "Firewall", True)
        self.ssh = self.check(layout, "Enable SSH", False)
        self.ntp = self.check(layout, "NTP time synchronization", True)
        self.trim = self.check(layout, "SSD TRIM timer", True)
        self.import_network = self.check(
            layout,
            "Copy live NetworkManager connections (includes Wi-Fi credentials)",
            False,
        )
        form = QFormLayout()
        self.dns = self.row(form, "DNS (IP, IP)", self.entry())
        layout.addLayout(form)

    def review_page(self, layout):
        self.review = QPlainTextEdit()
        self.review.setReadOnly(True)
        self.review.setMinimumHeight(430)
        layout.addWidget(self.review)
        save = QPushButton(self.tr("Save plan"))
        save.clicked.connect(self.save_plan)
        layout.addWidget(save)

    def install_page(self, layout):
        self.progress = QProgressBar()
        self.progress.setRange(0, 9)
        layout.addWidget(self.progress)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(15000)
        self.logs.setMinimumHeight(360)
        layout.addWidget(self.logs)
        save = QPushButton(self.tr("Save log"))
        save.clicked.connect(self.save_log)
        layout.addWidget(save)
        self.reboot = QPushButton(self.tr("Reboot"))
        self.reboot.setEnabled(False)
        self.reboot.clicked.connect(self.reboot_system)
        layout.addWidget(self.reboot)

    def config(self):
        disk = self.disk_box.currentData()
        if not disk:
            raise ValidationError("Select a disk / Wybierz dysk")
        if disk.mounted or disk.readonly:
            raise ValidationError(
                "Disk is in use/read-only / Dysk jest w użyciu lub tylko do odczytu"
            )
        return Config(
            device=disk.identity,
            firmware=self.hw["firmware"],
            mode=self.mode.currentData(),
            filesystem=self.fs.currentData(),
            partitions=self.parts if self.mode.currentData() == "manual" else [],
            bootloader=self.boot.currentData(),
            kernels=[k for k, box in self.kernels.items() if box.isChecked()],
            desktop=self.desktop.currentData(),
            gpu=self.gpu.currentData(),
            cpu=self.cpu.currentData(),
            packages=[
                self.selected.item(i).text() for i in range(self.selected.count())
            ],
            software=[name for name, box in self.software.items() if box.isChecked()],
            encrypt=self.encrypt.isChecked(),
            pbkdf=self.pbkdf.currentData(),
            swap_mib=self.swap.value(),
            zram=self.zram.isChecked(),
            snapshots=self.snapshots.isChecked(),
            groups=[s.strip() for s in self.user_groups.text().split(",") if s.strip()],
            username=self.username.text(),
            hostname=self.hostname.text(),
            locale=self.locale.currentData(),
            timezone=self.timezone.text(),
            keyboard=self.keyboard.currentData(),
            root_locked=self.root_locked.isChecked(),
            sudo=self.sudo.isChecked(),
            bluetooth=self.bluetooth.isChecked(),
            ssh=self.ssh.isChecked(),
            firewall=self.firewall.isChecked(),
            ntp=self.ntp.isChecked(),
            trim=self.trim.isChecked(),
            dns=[s.strip() for s in self.dns.text().split(",") if s.strip()],
            import_network=self.import_network.isChecked(),
        )

    def secrets(self):
        if (
            not self.root_locked.isChecked()
            and self.root_password.text() != self.root_repeat.text()
        ):
            raise ValidationError("Root passwords differ / Hasła root różnią się")
        if self.password.text() != self.password_repeat.text():
            raise ValidationError("Passwords differ / Hasła różnią się")
        if (
            self.encrypt.isChecked()
            and self.luks_pass.text() != self.luks_repeat.text()
        ):
            raise ValidationError(
                "Encryption passwords differ / Hasła szyfrowania różnią się"
            )
        result = Secrets(
            self.password.text(), self.root_password.text(), self.luks_pass.text()
        )
        result.validate(self.config())
        return result

    def goto(self, index):
        self.stack.setCurrentIndex(index)
        self.steps.setCurrentRow(index)
        self.back.setEnabled(index > 0 and index < 10)
        self.next.setEnabled(index < 10)
        self.next.setText(
            self.tr("Install") if index == 9 and not self.dry_run else self.tr("Next")
        )
        if index == 2:
            self.network_refresh()

    def advance(self):
        index = self.stack.currentIndex()
        if index == 9:
            self.begin()
            return
        if index == 3 and not self.disk_box.currentData():
            self.error("Select a disk / Wybierz dysk")
            return
        if index in {4, 5, 7}:
            try:
                self.config().validate()
                if index == 7 and not self.dry_run:
                    self.secrets()
            except Exception as exc:
                self.error(str(exc))
                return
        if index == 8:
            try:
                from arch_gui_installer.main import build_plan

                c = self.config()
                c.validate()
                if not self.dry_run:
                    self.secrets()
                self.next.setEnabled(False)

                def ready(plan):
                    self.plan = plan
                    self.review.setPlainText(self.plan_text())
                    self.goto(9)

                self.task(lambda: build_plan(c), ready)
            except Exception as exc:
                self.next.setEnabled(True)
                self.error(str(exc))
            return
        self.goto(index + 1)

    def plan_text(self):
        if not self.plan:
            return ""
        c = self.plan.config
        return (
            json.dumps(c["device"], indent=2)
            + "\n\n"
            + "\n".join(self.plan.operations)
            + "\n\n"
            + f"{c['desktop']} • {c['firmware']} • {c['bootloader']} • {c['username']}@{c['hostname']}\n\n"
            + "Packages (dependencies resolved before disk writes):\n"
            + " ".join(self.plan.packages)
            + "\n\nSHA256: "
            + self.plan.digest()
            + "\n\nNo automatic rollback. / Brak automatycznego cofania zmian."
        )

    def begin(self):
        if not self.plan:
            return
        if self.dry_run:
            self.goto(10)
            self.logs.setPlainText(
                "DRY RUN — read-only plan, no commands applied.\n\n" + self.plan_text()
            )
            return
        try:
            secrets = self.secrets()
        except Exception as exc:
            self.error(str(exc))
            return
        serial = (
            self.plan.config["device"]["serial"]
            or self.plan.config["device"]["wwn"]
            or self.plan.config["device"]["path"]
        )
        answer, ok = QInputDialog.getText(
            self,
            "Destructive operations / Operacje destrukcyjne",
            "Confirm reviewed disk changes by typing / Potwierdź zmiany wpisując:\n"
            + serial,
        )
        if not ok or answer != serial:
            secrets.clear()
            return
        self.temp = tempfile.TemporaryDirectory(prefix="easy-arch-plan-")
        plan_path = Path(self.temp.name) / "plan.json"
        plan_path.write_text(json.dumps(self.plan.to_dict()))
        plan_path.chmod(0o600)
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.proc.readyReadStandardOutput.connect(self.process_output)
        self.proc.readyReadStandardError.connect(
            lambda: self.logs.appendPlainText(
                bytes(self.proc.readAllStandardError()).decode(errors="replace")
            )
        )
        self.proc.finished.connect(self.process_finished)
        self.proc.errorOccurred.connect(
            lambda error: self.error(self.proc.errorString())
        )
        args = [
            sys.executable,
            "-I",
            "-m",
            "arch_gui_installer",
            "--apply",
            str(plan_path),
            "--approval",
            self.plan.digest(),
        ]
        executable = args.pop(0)
        if os.geteuid() != 0:
            if Path("/usr/lib/easy-arch/backend").is_file():
                args = ["/usr/lib/easy-arch/backend", *args[3:]]
            else:
                args.insert(0, executable)
            executable = "pkexec"
            args.insert(0, "--disable-internal-agent")
        self.proc.start(executable, args)
        self.proc.write(json.dumps(asdict(secrets)).encode() + b"\n")
        secrets.clear()
        for entry in [
            self.password,
            self.password_repeat,
            self.root_password,
            self.root_repeat,
            self.luks_pass,
            self.luks_repeat,
        ]:
            entry.clear()
        self.goto(10)
        self.status.setText("Installing / Instalacja…")

    def process_output(self):
        self.buffer += bytes(self.proc.readAllStandardOutput())
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                event = json.loads(line)
            except ValueError:
                self.logs.appendPlainText(line.decode(errors="replace"))
                continue
            if event.get("event") == "stage":
                self.status.setText(f"{event['name']}: {event['status']}")
                self.progress.setValue(
                    event["index"] if event["status"] == "done" else event["index"] - 1
                )
            if event.get("event") == "complete":
                self.succeeded = True
            self.logs.appendPlainText(
                event.get("text", json.dumps(event, ensure_ascii=False))
            )

    def process_finished(self, code, _):
        self.process_output()
        success = code == 0 and self.succeeded
        self.status.setText(
            "Completed / Zakończono"
            if success
            else "Failed or cancelled. Review logs. / Błąd lub anulowano. Sprawdź logi."
        )
        self.reboot.setEnabled(success)
        self.cancel.setText("Close / Zamknij")
        if self.temp:
            self.temp.cleanup()
            self.temp = None

    def save_plan(self):
        if self.plan:
            name, _ = QFileDialog.getSaveFileName(
                self, self.tr("Save plan"), "easy-arch-plan.json", "JSON (*.json)"
            )
            if name:
                Path(name).write_text(json.dumps(self.plan.to_dict(), indent=2))

    def save_log(self):
        name, _ = QFileDialog.getSaveFileName(
            self, self.tr("Save log"), "easy-arch-install.txt", "Text (*.txt)"
        )
        if name:
            Path(name).write_text(self.logs.toPlainText())
            Path(name).chmod(0o600)

    def cancel_install(self):
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.write(b'{"action":"cancel"}\n')
            self.status.setText(
                "Cancellation requested; waiting for current command. / Anulowanie po bieżącej komendzie."
            )
            self.cancel.setEnabled(False)
        else:
            self.close()

    def reboot_system(self):
        if (
            QMessageBox.question(
                self,
                self.tr("Reboot"),
                "Reboot this computer now? / Uruchomić ten komputer ponownie?",
            )
            == QMessageBox.StandardButton.Yes
        ):
            QProcess.startDetached("systemctl", ["reboot"])

    def closeEvent(self, event):
        if (self.proc and self.proc.state() != QProcess.ProcessState.NotRunning) or any(
            t.isRunning() for t in self.tasks
        ):
            event.ignore()
            self.status.setText(
                "Wait for operations to finish / Poczekaj na zakończenie operacji"
            )
        else:
            event.accept()


def launch(dry_run=False):
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Easy Arch")
    app.setStyle("Fusion")
    window = Wizard(dry_run)
    screen = app.primaryScreen()
    if screen:
        area = screen.availableGeometry()
        window.resize(
            min(1180, max(760, area.width() - 40)),
            min(800, max(540, area.height() - 60)),
        )
    window.show()
    return app.exec()
