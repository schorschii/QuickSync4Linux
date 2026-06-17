#!/usr/bin/env python3
"""QuickSync4Linux GUI – PySide6 version (drop-in replacement for the tkinter gui.py)"""

import json
import os
import re
import subprocess
import tempfile
import threading

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QLineEdit, QTextEdit, QFrame,
    QDialog, QDialogButtonBox, QMessageBox, QFileDialog,
    QFormLayout, QSizePolicy, QStatusBar, QAbstractItemView, QStyle,
    QTableWidget, QTableWidgetItem, QHeaderView, QListWidget,
    QListWidgetItem, QSplitter, QSpinBox, QRadioButton, QButtonGroup,
)
from PySide6.QtGui import QFont, QPixmap, QColor
from PySide6.QtCore import Qt, Signal, QObject, QTimer

from . import vcard
from . import btserial
from . import quicksync

from . import backend

# Reuse constants from backend
DEFAULT_LOG_FILE    = backend.DEFAULT_LOG_FILE
DEFAULT_CONFIG_FILE = backend.DEFAULT_CONFIG_FILE
BT_MAC_RE           = backend.BT_MAC_RE

def CHECK_TIMEOUT():       return backend.CHECK_TIMEOUT
def DISCOVER_TIMEOUT():    return backend.DISCOVER_TIMEOUT
def CLI_DEFAULT_TIMEOUT(): return backend.CLI_DEFAULT_TIMEOUT
def DEFAULT_BAUD():        return backend.DEFAULT_BAUD

# Module-level aliases for compatibility
CHECK_TIMEOUT       = backend.CHECK_TIMEOUT
DISCOVER_TIMEOUT    = backend.DISCOVER_TIMEOUT
CLI_DEFAULT_TIMEOUT = backend.CLI_DEFAULT_TIMEOUT
DEFAULT_BAUD        = backend.DEFAULT_BAUD

# ─── Backend error-code translation ───────────────────────────────────────────
_BT_ERROR_STRINGS: dict[str, str] = {
    'ERR_NOT_CONNECTED':      '✗ Device not connected — Please enable Bluetooth on your phone.',
    'ERR_NOT_REACHABLE_LOCKED': '✗ Device not reachable — Please turn on the screen and unlock your phone.',
    'ERR_NOT_REACHABLE':      '✗ Device not reachable — Please enable Bluetooth and turn on the screen of your phone.',
    'ERR_REFUSED':            '✗ Connection refused — Please enable Bluetooth on your phone.',
    'ERR_NOT_FOUND':          '✗ Device not found — Please enable Bluetooth on your phone.',
    'ERR_TIMEOUT':            '✗ Timeout — Please unlock your phone and try again.',
}

def _translate_err(widget, key: str) -> str:
    """Translate a backend error key through Qt tr() into the active locale."""
    src = _BT_ERROR_STRINGS.get(key)
    if src:
        return widget.tr(src)
    return key

def _make_dialog_buttons(widget, flags=None):
    """Creates a QDialogButtonBox with translatable button labels."""
    from PySide6.QtWidgets import QDialogButtonBox
    if flags is None:
        flags = QDialogButtonBox.Ok | QDialogButtonBox.Cancel
    btns = QDialogButtonBox(flags)
    ok_btn = btns.button(QDialogButtonBox.Ok)
    cancel_btn = btns.button(QDialogButtonBox.Cancel)
    close_btn = btns.button(QDialogButtonBox.Close)
    if ok_btn:     ok_btn.setText(widget.tr('OK'))
    if cancel_btn: cancel_btn.setText(widget.tr('Cancel'))
    if close_btn:  close_btn.setText(widget.tr('Close'))
    return btns


def simple_input(parent, title: str, label: str) -> str:
    from PySide6.QtWidgets import QInputDialog
    text, ok = QInputDialog.getText(parent, title, label)
    return text.strip() if ok else ''

class _WorkerSignals(QObject):
    append_text = Signal(str)
    status_update = Signal(str, str)
    connection_state = Signal(bool)
    action_finished = Signal(str, str)
    show_info = Signal(str, str)   # (title, text)
    clear_output = Signal()

class QuickSyncGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(self.tr('QuickSync4LinuxGui'))
        self.resize(720, 560)

        self._device_map: dict[str, str] = {}
        self._device_connected: bool | None = None
        self._signals = _WorkerSignals()
        self._signals.append_text.connect(self._append_output)
        self._signals.status_update.connect(self._update_status_bar)
        self._signals.connection_state.connect(self._set_connection_state)
        self._signals.action_finished.connect(self._parse_and_fill_ui)
        self._signals.show_info.connect(self._show_info_window)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # QSplitter: Seitenleiste verschiebbar wie in Dolphin
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(4)

        # Sidebar
        sidebar = QWidget()
        sidebar.setMinimumWidth(120)
        sidebar.setMaximumWidth(300)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(1)

        style = self.style()

        def sb_group(text):
            lbl = QLabel(text)
            lbl.setStyleSheet('color: palette(window-text); font-size: 13px; font-weight: bold; padding: 12px 4px 2px 4px;')
            sidebar_layout.addWidget(lbl)
            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            sep.setFrameShadow(QFrame.Plain)
            sidebar_layout.addWidget(sep)

        def sb_btn(label, icon_name, slot, danger=False, success=False):
            b = QPushButton(' ' + label)
            b.setIcon(style.standardIcon(icon_name))
            b.clicked.connect(slot)
            color = '#c9302c' if danger else ('#449d44' if success else 'palette(window-text)')
            b.setStyleSheet(f'text-align: left; padding: 4px 6px; border: none; color: {color};')
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            return b

        sb_group(self.tr('Device'))
        sidebar_layout.addWidget(sb_btn(self.tr('Connect'),   QStyle.SP_DialogApplyButton,     self.test_connection,   success=True))
        sidebar_layout.addWidget(sb_btn(self.tr('Disconnect'),     QStyle.SP_DialogCancelButton,    self.disconnect_device, danger=True))
        sidebar_layout.addWidget(sb_btn(self.tr('Info'),        QStyle.SP_MessageBoxInformation, lambda: self.run_action('info')))
        sidebar_layout.addWidget(sb_btn(self.tr(self.tr('Obex Info')),   QStyle.SP_MessageBoxInformation, lambda: self.run_action('obexinfo')))

        sb_group(self.tr('Contacts'))
        sidebar_layout.addWidget(sb_btn(self.tr(self.tr('Manage Contacts')),   QStyle.SP_FileDialogDetailedView, self.open_contacts_manager))
        sidebar_layout.addWidget(sb_btn(self.tr('Export'), QStyle.SP_ArrowDown,              self.get_contacts))
        sidebar_layout.addWidget(sb_btn(self.tr('Import'), QStyle.SP_ArrowUp,               lambda: self.choose_file_and_run('createcontacts')))

        sb_group(self.tr('Files'))
        sidebar_layout.addWidget(sb_btn(self.tr('File Manager'), QStyle.SP_DirOpenIcon, self.open_file_manager))

        sb_group(self.tr('Settings'))
        sidebar_layout.addWidget(sb_btn(self.tr(self.tr('Timeouts')), QStyle.SP_DialogHelpButton, self.open_settings_timeouts))
        sidebar_layout.addWidget(sb_btn(self.tr('Baudrate'), QStyle.SP_DialogHelpButton, self.open_settings_baudrate))
        sidebar_layout.addWidget(sb_btn(self.tr('Language'), QStyle.SP_DialogHelpButton, self.open_settings_language))

        sb_group(self.tr('Log / Console'))
        sidebar_layout.addWidget(sb_btn(self.tr('Open Log'), QStyle.SP_FileIcon, self.open_log))

        sidebar_layout.addStretch()
        splitter.addWidget(sidebar)

        # Right-hand side as a splitter widget
        right_widget = QWidget()
        right_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_col = QVBoxLayout(right_widget)
        right_col.setSpacing(6)
        right_col.setContentsMargins(6, 0, 0, 0)

        # Connection type selector
        conn_type_row = QHBoxLayout()
        self._conn_type_group = QButtonGroup(self)
        self._rb_bt = QRadioButton('Bluetooth')
        self._rb_serial = QRadioButton(self.tr('Serial'))
        self._rb_bt.setChecked(True)
        self._conn_type_group.addButton(self._rb_bt)
        self._conn_type_group.addButton(self._rb_serial)
        conn_type_row.addWidget(self._rb_bt)
        conn_type_row.addWidget(self._rb_serial)
        conn_type_row.addStretch()
        right_col.addLayout(conn_type_row)

        dev_row_widget = QWidget()
        dev_row_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        dev_row = QHBoxLayout(dev_row_widget)
        dev_row.setContentsMargins(0, 0, 0, 0)
        dev_row.setSpacing(4)
        self.device = QComboBox()
        self.device.setEditable(False)
        self.device.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        dev_row.addWidget(self.device, stretch=1)

        btn_refresh = QPushButton('⟳')
        btn_refresh.setFixedWidth(32)
        btn_refresh.clicked.connect(self.refresh_devices_and_check)
        dev_row.addWidget(btn_refresh)

        self.baud = QLineEdit(backend.DEFAULT_BAUD)
        self.baud.setVisible(False)
        right_col.addWidget(dev_row_widget)

        # Switch connection type
        self._rb_bt.toggled.connect(self._on_conn_type_changed)
        self._rb_serial.toggled.connect(self._on_conn_type_changed)

        form_layout = QFormLayout()
        form_layout.setSpacing(6)

        self.ui_hersteller = QLineEdit("-"); self.ui_hersteller.setReadOnly(True)
        self.ui_modell = QLineEdit("-"); self.ui_modell.setReadOnly(True)
        self.ui_mac = QLineEdit("-"); self.ui_mac.setReadOnly(True)
        self.ui_firmware = QLineEdit("-"); self.ui_firmware.setReadOnly(True)
        self.ui_seriennummer = QLineEdit("-"); self.ui_seriennummer.setReadOnly(True)
        self.ui_kontakt_anzahl = QLineEdit("-"); self.ui_kontakt_anzahl.setReadOnly(True)

        form_layout.addRow(self.tr("<b>Device Information</b>"), QLabel(""))
        form_layout.addRow(self.tr("Manufacturer:"), self.ui_hersteller)
        form_layout.addRow(self.tr("Model / Product:"), self.ui_modell)
        form_layout.addRow(self.tr("MAC Address:"), self.ui_mac)
        form_layout.addRow(self.tr("Firmware Version:"), self.ui_firmware)
        form_layout.addRow(self.tr("Serial Number (IPUI):"), self.ui_seriennummer)
        form_layout.addRow(self.tr("Contact Count:"), self.ui_kontakt_anzahl)
        right_col.addLayout(form_layout)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        right_col.addWidget(line)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFont('Monospace', 9))
        right_col.addWidget(self.output, stretch=1)
        self._signals.clear_output.connect(self.output.clear)

        splitter.addWidget(right_widget)
        # Anfangsbreiten: Sidebar 160px, Rest bekommt den verbleibenden Platz
        splitter.setSizes([160, 600])
        # Splitter-Position beim Beenden speichern und beim Start wiederherstellen
        self._splitter = splitter
        root.addWidget(splitter, stretch=1)
        self.set_log_file(DEFAULT_LOG_FILE)

        # Splitter-Position aus den Settings laden
        from PySide6.QtCore import QSettings
        _s = QSettings('QuickSync4LinuxGui', 'QuickSync4LinuxGui')
        splitter_state = _s.value('mainSplitterState')
        if splitter_state:
            self._splitter.restoreState(splitter_state)

        sb = QStatusBar()
        self.setStatusBar(sb)
        self._status_dot = QLabel('●')
        self._status_dot.setStyleSheet('color: gray;')
        self._status_label = QLabel(self.tr('Checking device status…'))
        sb.addWidget(self._status_dot)
        sb.addWidget(self._status_label, 1)

        self.refresh_devices()
        QTimer.singleShot(500, self.check_device_connection)

    def _parse_and_fill_ui(self, action, text):
        if not text:
            return

        if action == 'contact_count':
            self.ui_kontakt_anzahl.setText(text)
            return

        def find_val(pattern, string):
            match = re.search(pattern, string, re.IGNORECASE)
            return match.group(1).strip() if match else None

        current_dev = self.current_device()
        if BT_MAC_RE.match(current_dev or ''):
            self.ui_mac.setText(current_dev.split('@')[0])

        hersteller = find_val(r'(?:Hersteller|Manufacturer)\s*:\s*(.*)', text)
        modell = find_val(r'(?:Modell|Model|Product)\s*:\s*(.*)', text)
        mac = find_val(r'(?:MAC-Adresse|MAC)\s*:\s*(.*)', text)
        firmware = find_val(r'(?:Firmware-Version|Firmware)\s*:\s*([0-9\.]+)', text)
        seriennummer = find_val(r'(?:Seriennummer|Serial(?:\s*\(IPUI\))?)\s*:\s*(.*)', text)
        anzahl = find_val(r'(?:Received|Found|Total)\s*([0-9]+)\s*(?:contacts|Kontakte)', text)

        if hersteller: self.ui_hersteller.setText(hersteller)
        if modell: self.ui_modell.setText(modell.split(',')[-1].strip() if ',' in modell else modell)
        if mac: self.ui_mac.setText(mac)
        if firmware: self.ui_firmware.setText(firmware)
        if seriennummer: self.ui_seriennummer.setText(seriennummer)
        if anzahl: self.ui_kontakt_anzahl.setText(anzahl)

    def _on_conn_type_changed(self):
        """Switch between Bluetooth and serial mode."""
        if self._rb_bt.isChecked():
            self.device.setEditable(False)
        else:
            self.device.setEditable(True)
        self.refresh_devices()

    def refresh_devices(self, prefer=None):
        self.device.blockSignals(True)
        self.device.clear()

        if self._rb_serial.isChecked():
            # Show serial devices
            import glob
            serial_ports = sorted(
                glob.glob('/dev/ttyACM*') +
                glob.glob('/dev/ttyUSB*') +
                glob.glob('/dev/rfcomm*')
            )
            self._device_map = {p: p for p in serial_ports}
            for p in serial_ports:
                self.device.addItem(p)
            self.device.setEditable(True)
            if prefer and prefer in serial_ports:
                self.device.setCurrentText(prefer)
            elif serial_ports:
                self.device.setCurrentIndex(0)
        else:
            # Show Bluetooth devices
            raw = backend.discover_devices()
            entries = [(f'{label} ({mac}) [Bluetooth]', mac) for mac, label in raw]
            self._device_map = {display: mac for display, mac in entries}
            for display, _ in entries:
                self.device.addItem(display)
            self.device.setEditable(False)

            if prefer:
                for display, mac in entries:
                    if mac == prefer:
                        self.device.setCurrentText(display)
                        self.device.blockSignals(False)
                        return

            # Prefer Gigaset devices
            for display, mac in entries:
                if 'gigaset' in display.lower():
                    self.device.setCurrentText(display)
                    self.device.blockSignals(False)
                    return

            if entries:
                self.device.setCurrentIndex(0)
            elif prefer:
                self.device.setCurrentText(prefer)

        self.device.blockSignals(False)

    def refresh_devices_and_check(self):
        self.refresh_devices()
        self.check_device_connection()

    def current_device(self):
        text = self.device.currentText().strip()
        return self._device_map.get(text, text)

    def current_device_label(self):
        text = self.device.currentText().strip()
        for suffix in (' [Bluetooth]', ' (Serial)'):
            if text.endswith(suffix):
                return text[: -len(suffix)]
        return text or self.current_device()

    def check_connection_or_warn(self) -> bool:
        if not self.current_device():
            QMessageBox.warning(self, self.tr('No Device'), self.tr('Please select a device first.'))
            return False
        if self._device_connected is not True:
            QMessageBox.warning(self, self.tr('Not Connected'), self.tr('Please connect to a device first.'))
            return False
        return True

    def _open_connection(self):
        """Open a direct connection to the current device."""
        dev = self.current_device()
        if not dev:
            raise RuntimeError(self.tr('No device selected.'))
        baud = int(self.baud.text()) if self.baud.text() else 9600
        return quicksync.open_connection(dev, baud)

    def _set_connection_state(self, connected: bool):
        self._device_connected = connected

    def is_device_connected(self):
        return bool(self.current_device()) and self._device_connected is not False

    def _append_output(self, text: str):
        if text == 'clear':
            self.output.clear()
            return
        self.output.append(text)
        self._log_raw_output(text)

    def _log_raw_output(self, text: str):
        backend.log(text)

    
    def set_log_file(self, path: str | None = None):
        self._append_output(self.tr('Log file: {} (max. 1 MB, 3 backups)').format(backend.DEFAULT_LOG_FILE))

    def _update_status_bar(self, text: str, colour: str):
        self._status_dot.setStyleSheet(f'color: {colour};')
        self._status_label.setText(text)

    def _show_info_window(self, title, text):
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(520, 400)
        layout = QVBoxLayout(dlg)
        out = QTextEdit()
        out.setReadOnly(True)
        out.setFont(QFont('Monospace', 9))
        out.setPlainText(text)
        layout.addWidget(out)
        btn = _make_dialog_buttons(self, QDialogButtonBox.Close)
        btn.rejected.connect(dlg.reject)
        layout.addWidget(btn)
        dlg.exec()

    def run_action(self, action, options=None, file=None):
        dev = self.current_device()
        if not dev:
            self.output.clear()
            self._append_output(self.tr('✗ No device selected or connection lost'))
            return

        if self._device_connected is False and action not in ('info', 'obexinfo'):
            QMessageBox.warning(self, self.tr('Connection Lost'),
                self.tr('The device is currently disconnected. Please reconnect.'))
            return

        self.output.clear()
        sig = self._signals

        def worker():
            ser = None
            try:
                ser = self._open_connection()
                if action == 'info':
                    result = quicksync.get_info(ser)
                    sig.show_info.emit(self.tr('Device Info'), result)
                    sig.clear_output.emit()
                    sig.action_finished.emit('info', result)
                elif action == 'obexinfo':
                    result = quicksync.get_obex_info(ser)
                    sig.show_info.emit(self.tr('Obex Info'), result)
                    sig.clear_output.emit()
                    sig.action_finished.emit('obexinfo', result)
                elif action == 'getcontacts':
                    if file:
                        vcf = quicksync.get_contacts(ser)
                        with open(file, 'wb') as f:
                            f.write(vcf)
                        sig.action_finished.emit('getcontacts', vcf.decode('utf-8', errors='replace'))
                elif action in ('setcontacts', 'createcontacts'):
                    if file:
                        with open(file, 'rb') as f:
                            vcf = f.read()
                        quicksync.set_contacts(ser, vcf)
                elif action == 'download':
                    if options and file:
                        quicksync.download_file(ser, options, file)
                elif action == 'upload':
                    if options and file:
                        quicksync.upload_file(ser, options, file)
                elif action == 'delete':
                    if options:
                        quicksync.delete_file(ser, options)
                else:
                    sig.append_text.emit(f'{self.tr("Unknown action")}: {action}')
            except Exception as e:
                sig.append_text.emit(f'✗ {self.tr("Error")}: {e}')
            finally:
                if ser:
                    quicksync.close_connection(ser)

        threading.Thread(target=worker, daemon=True).start()

    def choose_file_and_run(self, action):
        if not self.check_connection_or_warn():
            return
        path, _ = QFileDialog.getOpenFileName(self, self.tr('Choose VCF file'), os.path.expanduser('~'), self.tr('VCF files (*.vcf);;All files (*)'))
        if path:
            self.run_action(action, None, path)

    def get_contacts(self):
        if not self.check_connection_or_warn():
            return
        path, _ = QFileDialog.getSaveFileName(self, self.tr('Save contacts as'), os.path.expanduser('~'), self.tr('VCF files (*.vcf);;All files (*)'))
        if path:
            self.run_action('getcontacts', None, path)

    
    def open_log(self):
        # 1. Nutze das Attribut der GUI, falls gesetzt, andernfalls direkt das Backend-Standard-Log
        log_path = getattr(self, '_log_file', None) or backend.DEFAULT_LOG_FILE
        
        # 2. Resolve the path absolutely and expand the tilde
        path_target = os.path.abspath(os.path.expanduser(log_path))
        
        # 3. Determine the folder, regardless of whether a file or folder was passed
        initial_dir = path_target if os.path.isdir(path_target) else os.path.dirname(path_target)

        # Safety net for fresh installs where the folder does not exist yet
        if not os.path.exists(initial_dir):
            try:
                os.makedirs(initial_dir, exist_ok=True)
            except Exception:
                initial_dir = os.path.expanduser('~')

        # Open the dialog in the correct directory
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr('Choose log file'), initial_dir, self.tr('Log files (*.log);;All files (*)')
        )
        
        if not path:
            return
        try:
            subprocess.Popen(['xdg-open', path])
        except Exception as e:
            self._append_output(f'✗ {self.tr("Could not open log file")}: {e}')

    def open_settings_timeouts(self):
        dlg = QDialog(self)
        dlg.setWindowTitle(self.tr('Settings'))
        layout = QVBoxLayout(dlg)
        form = QFormLayout()
        from PySide6.QtWidgets import QSpinBox
        chk = QSpinBox(); chk.setRange(1, 3600); chk.setValue(backend.CHECK_TIMEOUT); form.addRow(self.tr('Connection timeout (s):'), chk)
        dsk = QSpinBox(); dsk.setRange(1, 3600); dsk.setValue(backend.DISCOVER_TIMEOUT); form.addRow(self.tr('Bluetooth timeout (s):'), dsk)
        cli = QSpinBox(); cli.setRange(1, 36000); cli.setValue(backend.CLI_DEFAULT_TIMEOUT); form.addRow(self.tr('CLI timeout (s):'), cli)
        layout.addLayout(form)
        btns = _make_dialog_buttons(self)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject); layout.addWidget(btns)
        if dlg.exec() == QDialog.Accepted:
            backend.CHECK_TIMEOUT = chk.value()
            backend.DISCOVER_TIMEOUT = dsk.value()
            backend.CLI_DEFAULT_TIMEOUT = cli.value()
            backend.save_settings()

    def open_settings_baudrate(self):
        dlg = QDialog(self)
        dlg.setWindowTitle(self.tr('Settings'))
        layout = QVBoxLayout(dlg)
        form = QFormLayout()
        from PySide6.QtWidgets import QComboBox as _CB
        baud_combo = _CB()
        baud_combo.setEditable(True)
        for b in ['1200', '2400', '4800', '9600', '19200', '38400', '57600', '115200']:
            baud_combo.addItem(b)
        baud_combo.setCurrentText(backend.DEFAULT_BAUD)
        form.addRow(self.tr('Baud rate (Serial):'), baud_combo)
        layout.addLayout(form)
        btns = _make_dialog_buttons(self)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject); layout.addWidget(btns)
        if dlg.exec() == QDialog.Accepted:
            backend.DEFAULT_BAUD = baud_combo.currentText()
            self.baud.setText(baud_combo.currentText())
            backend.save_settings()

    def open_settings_language(self):
        from PySide6.QtCore import QSettings, QTranslator, QLocale
        dlg = QDialog(self)
        dlg.setWindowTitle(self.tr('Language'))
        layout = QVBoxLayout(dlg)
        form = QFormLayout()
        lang_combo = QComboBox()
        lang_combo.addItem('English', 'en')
        lang_combo.addItem('Deutsch', 'de')
        _s = QSettings('QuickSync4LinuxGui', 'QuickSync4LinuxGui')
        current_lang = _s.value('language', '')
        idx = lang_combo.findData(current_lang)
        if idx >= 0:
            lang_combo.setCurrentIndex(idx)
        form.addRow(self.tr('Language') + ':', lang_combo)
        layout.addLayout(form)
        info = QLabel(self.tr('The language change takes effect after restarting the application.'))
        info.setWordWrap(True)
        layout.addWidget(info)
        btns = _make_dialog_buttons(self)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject); layout.addWidget(btns)
        if dlg.exec() == QDialog.Accepted:
            selected_lang = lang_combo.currentData()
            _s.setValue('language', selected_lang)

    def open_file_manager(self):
        if not self.check_connection_or_warn():
            return
        if hasattr(self, '_file_manager_win') and self._file_manager_win is not None:
            self._file_manager_win.raise_()
            self._file_manager_win.activateWindow()
            return
        self._file_manager_win = FileManagerWindow(self)
        self._file_manager_win.finished.connect(lambda: setattr(self, '_file_manager_win', None))

    def open_contacts_manager(self):
        if not self.check_connection_or_warn():
            return
        if hasattr(self, '_contacts_win') and self._contacts_win is not None:
            self._contacts_win.raise_()
            self._contacts_win.activateWindow()
            return
        self._contacts_win = ContactsWindow(self)
        self._contacts_win.finished.connect(lambda: setattr(self, '_contacts_win', None))

    def open_file_manager(self):
        if not self.check_connection_or_warn():
            return
        if hasattr(self, '_file_manager_win') and self._file_manager_win is not None:
            self._file_manager_win.raise_()
            self._file_manager_win.activateWindow()
            return
        self._file_manager_win = FileManagerWindow(self)
        self._file_manager_win.finished.connect(lambda: setattr(self, '_file_manager_win', None))



    def download_file(self):
        remote = simple_input(self, self.tr('Remote file'), self.tr('Enter remote file name:'))
        if not remote: return
        out, _ = QFileDialog.getSaveFileName(self, self.tr('Save as'))
        if out: self.run_action('download', remote, out)

    def upload_file(self):
        path, _ = QFileDialog.getOpenFileName(self, self.tr('Choose file to upload'))
        if not path: return
        remote = simple_input(self, self.tr('Remote name'), self.tr('Remote file name on the device:'))
        if not remote: return
        self.run_action('upload', remote, path)

    def delete_file(self):
        remote = simple_input(self, self.tr('Remote file'), self.tr('Remote file name to delete:'))
        if remote: self.run_action('delete', remote)

    def check_device_connection(self):
        label = self.current_device_label()
        self._update_status_bar(f'{label}: {self.tr("Checking device status…")}' if label else self.tr('Checking device status…'), '#888888')
        sig = self._signals

        def worker():
            connected = False
            status_text = self.tr('No device connected')
            status_color = '#d9534f'
            ser = None
            dev = self.current_device()
            try:
                if dev:
                    ser = self._open_connection()
                    info_text = quicksync.get_info(ser)
                    status_text = f'{label} {self.tr("connected")}' if label else self.tr('Device connected')
                    status_color = '#5cb85c'
                    connected = True
                    sig.append_text.emit(f'✓ {status_text}')
                    self._log_raw_output(info_text)
                    sig.action_finished.emit('info', info_text)
                    try:
                        vcf_bytes = quicksync.get_contacts(ser)
                        count = len(vcard.parseCards(vcf_bytes.decode('utf-8', errors='replace')))
                        sig.action_finished.emit('contact_count', str(count))
                    except Exception:
                        pass
            except Exception as e:
                key = backend.interpret_connection_error(str(e), dev)
                msg = _translate_err(self, key) if key else f'✗ {self.tr("Error")}: {e}'
                sig.append_text.emit(msg)
                status_text = msg
                status_color = '#d9534f'
            finally:
                if ser:
                    quicksync.close_connection(ser)
            sig.connection_state.emit(connected)
            sig.status_update.emit(status_text, status_color)

        threading.Thread(target=worker, daemon=True).start()

    def test_connection(self):
        self.output.clear()
        self._append_output(self.tr('--- Establishing connection ---'))
        label = self.current_device_label()
        self._update_status_bar(f'{label}: {self.tr("--- Establishing connection ---")}' if label else self.tr('--- Establishing connection ---'), '#888888')
        sig = self._signals

        def worker():
            connected = False
            result = ''
            ser = None
            dev = self.current_device()
            try:
                if not dev:
                    result = self.tr('No device selected.')
                else:
                    ser = self._open_connection()
                    info_text = quicksync.get_info(ser)
                    result = f'✓ {self.tr("Device connected")}: {label or dev}'
                    connected = True
                    self._log_raw_output(info_text)
                    sig.action_finished.emit('info', info_text)
            except Exception as e:
                key = backend.interpret_connection_error(str(e), dev)
                result = _translate_err(self, key) if key else f'✗ {self.tr("Connection to")} {label} ({dev}) {self.tr("failed")}: {e}'
            finally:
                if ser:
                    quicksync.close_connection(ser)

            status = ((f'{label} {self.tr("connected")}' if label else self.tr('Device connected'), '#5cb85c') if connected else (self.tr('No device connected'), '#d9534f'))
            sig.connection_state.emit(connected)
            sig.append_text.emit(result)
            sig.status_update.emit(*status)

        threading.Thread(target=worker, daemon=True).start()

    def closeEvent(self, event):
        from PySide6.QtCore import QSettings
        _s = QSettings('QuickSync4LinuxGui', 'QuickSync4LinuxGui')
        _s.setValue('mainSplitterState', self._splitter.saveState())
        super().closeEvent(event)

    def disconnect_device(self):
        dev = self.current_device()
        label = self.current_device_label() or dev
        if dev and btserial.isBluetoothAddress(dev):
            try: subprocess.run(['bluetoothctl', 'disconnect', dev.split('@')[0]], capture_output=True, timeout=5)
            except Exception: pass
        self.output.clear()
        self._append_output(f'✓ {self.tr("Disconnected from")} {label}')
        self._set_connection_state(False)
        self._update_status_bar(self.tr('No device connected'), '#d9534f')

# ─── Additional window classes ────────────────────────────────────────────────

class _FileManagerSignals(QObject):
    setup_ui     = Signal(list, str)
    set_status   = Signal(str)
    do_reload    = Signal()
    show_preview = Signal(str)   # tmp_path
    show_preview_err = Signal(str)  # error text

class FileManagerWindow(QDialog):
    """File manager with a KDE/Dolphin-style layout and fixed Gigaset parser."""

    IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif'}

    def __init__(self, parent):
        super().__init__(parent)
        self.parent_win = parent
        self.setWindowTitle(self.tr('File Manager'))
        self.resize(950, 550)
        self._files: list[dict] = []
        self._all_files: list[dict] = []
        self._fm_signals = _FileManagerSignals()

        # Hauptlayout (Vertikal)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # 1. Toolbar (Dolphin-like)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(4, 2, 4, 2)
        from PySide6.QtWidgets import QStyle
        
        def tbtn(label, icon_name, slot, is_danger=False):
            b = QPushButton(' ' + label)
            b.setIcon(self.style().standardIcon(icon_name))
            b.clicked.connect(slot)
            if is_danger:
                b.setStyleSheet("QPushButton { color: #cc241d; }")
            else:
                b.setStyleSheet("QPushButton { text-align: left; }")
            return b

        btn_reload = tbtn(self.tr('Reload'), QStyle.SP_BrowserReload, self.reload)
        btn_download = tbtn(self.tr('Download'), QStyle.SP_ArrowDown, self.download_selected)
        btn_upload = tbtn(self.tr('Upload'), QStyle.SP_ArrowUp, self.upload_file)
        btn_delete = tbtn(self.tr('Delete'), QStyle.SP_TrashIcon, self.delete_selected, is_danger=True)
        
        toolbar.addWidget(btn_reload)
        toolbar.addWidget(btn_download)
        toolbar.addWidget(btn_upload)
        toolbar.addWidget(btn_delete)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # Separator below toolbar
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Plain)
        layout.addWidget(sep)

        # 2. Haupt-Inhaltsbereich mit Dreifach-Splitting (Sidebar | Tabelle | Info-Panel)
        main_hbox = QHBoxLayout()
        main_hbox.setSpacing(6)

        # Left: places/folders sidebar (KDE style)
        from PySide6.QtWidgets import QListWidget, QListWidgetItem
        self.folder_sidebar = QListWidget()
        self.folder_sidebar.setFixedWidth(180)
        self.folder_sidebar.setStyleSheet("QListWidget { background: palette(window); border: none; font-weight: bold; }")
        self.folder_sidebar.itemClicked.connect(self._on_sidebar_folder_changed)
        main_hbox.addWidget(self.folder_sidebar)

        # Vertical separator
        v_sep1 = QFrame()
        v_sep1.setFrameShape(QFrame.VLine)
        v_sep1.setFrameShadow(QFrame.Plain)
        main_hbox.addWidget(v_sep1)

        # Mitte: Die Dateitabelle
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([self.tr('Name'), self.tr('Date'), self.tr('Size')])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setColumnWidth(0, 300)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(2, 90)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_selection)
        main_hbox.addWidget(self.table, stretch=2)

        # Vertical separator
        v_sep2 = QFrame()
        v_sep2.setFrameShape(QFrame.VLine)
        v_sep2.setFrameShadow(QFrame.Plain)
        main_hbox.addWidget(v_sep2)

        # Rechts: Dolphins Informations-Panel (Vorschau)
        preview_panel = QWidget()
        preview_panel.setFixedWidth(220)
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(4, 0, 4, 0)

        self.preview_label = QLabel(self.tr('No selection'))
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setStyleSheet('font-weight: bold; color: palette(window-text);')
        
        self.preview_image = QLabel()
        self.preview_image.setAlignment(Qt.AlignCenter)
        self.preview_image.setMinimumHeight(180)
        self.preview_image.setStyleSheet('background: palette(base); border: 1px solid palette(mid); border-radius: 4px;')

        self.preview_info = QTextEdit()
        self.preview_info.setReadOnly(True)
        self.preview_info.setFont(QFont('Monospace', 8))
        self.preview_info.setStyleSheet("QTextEdit { border: none; background: transparent; }")
        self.preview_info.setMaximumHeight(120)

        preview_layout.addWidget(self.preview_label)
        preview_layout.addWidget(self.preview_image)
        preview_layout.addWidget(self.preview_info)
        preview_layout.addStretch()
        main_hbox.addWidget(preview_panel)

        layout.addLayout(main_hbox, stretch=1)

        # Separator above status bar
        sep_bottom = QFrame()
        sep_bottom.setFrameShape(QFrame.HLine)
        sep_bottom.setFrameShadow(QFrame.Plain)
        layout.addWidget(sep_bottom)

        # 3. Statuszeile
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(6, 2, 6, 2)
        self.status_label = QLabel('')
        self.space_label = QLabel('')
        self.space_label.setStyleSheet('color: palette(mid); font-size: 11px;')
        bottom_row.addWidget(self.status_label)
        bottom_row.addStretch()
        bottom_row.addWidget(self.space_label)
        layout.addLayout(bottom_row)

        self.setModal(False)
        # Signals jetzt verbinden, da status_label und space_label bereits existieren
        self._fm_signals.setup_ui.connect(self._setup_ui_data)
        self._fm_signals.set_status.connect(self.status_label.setText)
        self._fm_signals.do_reload.connect(self.reload)
        self._fm_signals.show_preview.connect(self._show_preview)
        self._fm_signals.show_preview_err.connect(self.preview_image.setText)
        self.show()
        QTimer.singleShot(50, self.reload)

    def reload(self):
        self.status_label.setText(self.tr('Loading file list …'))
        self.space_label.setText('')
        self.table.setRowCount(0)
        self.folder_sidebar.clear()
        self._all_files = []
        self._files = []
        
        fm_sig = self._fm_signals

        def worker():
            ser = None
            log = self.parent_win._signals
            try:
                log.append_text.emit(self.tr('Loading file list …'))
                fm_sig.set_status.emit(self.tr('Loading file list …'))
                ser = self.parent_win._open_connection()
                output = quicksync.list_files(ser)

                try:
                    files = self._parse_listfiles(output)
                except Exception as parse_exc:
                    fm_sig.set_status.emit(f'✗ {self.tr("Parse error")}: {parse_exc}')
                    return

                import re as _re
                total = _re.search(r'Total Space:\s*([\d\.]+\s*\w+)', output, _re.IGNORECASE)
                free  = _re.search(r'Free Space:\s*([\d\.]+\s*\w+)',  output, _re.IGNORECASE)
                space_text = f'Free: {free.group(1)}  |  Total: {total.group(1)}' if total and free else ''

                if not files:
                    fm_sig.set_status.emit(self.tr('✗ No files found.'))
                    return

                log.append_text.emit(self.tr('✓ File list loaded: {} file(s) in {} folder(s)').format(len(files), len(set(f["folder"] for f in files))))
                fm_sig.setup_ui.emit(files, space_text)
            except Exception as e:
                dev = self.parent_win.current_device()
                key = backend.interpret_connection_error(str(e), dev)
                msg = _translate_err(self, key) if key else f'✗ {self.tr("Error")}: {e}'
                log.append_text.emit(f'[FileManager] {msg}')
                fm_sig.set_status.emit(msg)
            finally:
                if ser:
                    quicksync.close_connection(ser)
        threading.Thread(target=worker, daemon=True).start()

    def _parse_listfiles(self, text):
        import re as _re
        files = []
        current_folder = '/'
        
        # Replace stubborn non-breaking spaces (NBSP) with regular spaces
        clean_text = text.replace('\xa0', ' ')
        
        # Robust regex matching IDs, date formats, and file-size suffixes
        line_re = _re.compile(r'^(\d+):\s+(.+?)\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+[A-Z]\s+([\d\.]+\s+\w+)')

        for line in clean_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            
            # Detect folder changes, e.g. === /Pictures
            if stripped.startswith('==='):
                current_folder = stripped.replace('===', '').strip()
                continue
                
            m = line_re.match(stripped)
            if m:
                file_id, name, date_str, time_str, size_str = m.groups()
                files.append({
                    'id':     file_id,
                    'folder': current_folder,
                    'name':   name.strip(),
                    'date':   f"{date_str} {time_str}",
                    'size':   size_str,
                })
        return files

    def _setup_ui_data(self, files, space_text):
        self._all_files = files
        self.space_label.setText(space_text)
        folders = sorted(list(set(f['folder'] for f in files)))
        if not folders:
            folders = ['/']
        from PySide6.QtWidgets import QStyle, QListWidgetItem as _LWI
        self.folder_sidebar.clear()
        for folder in folders:
            item = _LWI(self.style().standardIcon(QStyle.SP_DirIcon), folder)
            self.folder_sidebar.addItem(item)
        if self.folder_sidebar.count() > 0:
            self.folder_sidebar.setCurrentRow(0)
            self._filter_by_folder(self.folder_sidebar.item(0).text())
        else:
            self.status_label.setText(self.tr('✗ No files found.'))

    def _on_sidebar_folder_changed(self, item):
        self._filter_by_folder(item.text())

    def _filter_by_folder(self, folder_name):
        self._files = [f for f in self._all_files if f['folder'] == folder_name]
        self._populate(self._files)

    def _populate(self, files):
        self.table.setRowCount(0)
        from PySide6.QtWidgets import QStyle as _QStyle
        img_exts = self.IMAGE_EXTS
        for f in files:
            row = self.table.rowCount()
            self.table.insertRow(row)
            
            ext = os.path.splitext(f['name'])[1].lower()
            icon_type = _QStyle.SP_FileDialogDetailedView if ext in img_exts else _QStyle.SP_FileIcon
            icon = self.style().standardIcon(icon_type)
            
            name_item = QTableWidgetItem(f['name'])
            name_item.setIcon(icon)
            
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(f.get('date', '-')))
            self.table.setItem(row, 2, QTableWidgetItem(f['size']))
            
        self.table.resizeRowsToContents()
        current_folder = self.folder_sidebar.currentItem().text() if self.folder_sidebar.currentItem() else ""
        self.status_label.setText(self.tr('{} file(s) in "{}"').format(len(files), current_folder))

    def _selected_file(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._files):
            return None
        return self._files[row]

    def _on_selection(self):
        f = self._selected_file()
        if not f:
            self.preview_image.clear()
            self.preview_label.setText(self.tr('No selection'))
            self.preview_info.clear()
            return
        ext = os.path.splitext(f['name'])[1].lower()
        self.preview_label.setText(f['name'])
        self.preview_info.setText(
            f"<b>ID:</b> {f['id']}<br>"
            f"<b>{self.tr('Path')}:</b> {f['folder']}/{f['name']}<br>"
            f"<b>{self.tr('Size')}:</b> {f['size']}<br>"
            f"<b>{self.tr('Date')}:</b> {f['date']}"
        )
        if ext in self.IMAGE_EXTS:
            self.preview_image.setText(f'⏳ {self.tr("Loading image …")}')
            self._load_preview(f)
        else:
            self.preview_image.setText(self.tr('No preview available'))

    def _load_preview(self, f):
        fm_sig = self._fm_signals
        def worker():
            ser = None
            try:
                fd, tmp_path = tempfile.mkstemp(suffix=os.path.splitext(f['name'])[1])
                os.close(fd)
                remote_path = f"{f['folder']}/{f['name']}"
                ser = self.parent_win._open_connection()
                quicksync.download_file(ser, remote_path, tmp_path)
                if os.path.exists(tmp_path):
                    fm_sig.show_preview.emit(tmp_path)
                else:
                    fm_sig.show_preview_err.emit('✗ Preview not available')
            except Exception as e:
                fm_sig.show_preview_err.emit(f'✗ {e}')
            finally:
                if ser:
                    quicksync.close_connection(ser)
        threading.Thread(target=worker, daemon=True).start()

    def _show_preview(self, path):
        pixmap = QPixmap(path)
        try:
            os.unlink(path)
        except OSError:
            pass
        if pixmap.isNull():
            self.preview_image.setText(f'✗ {self.tr("Image error")}')
            return
        scaled = pixmap.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview_image.setPixmap(scaled)

    def download_selected(self):
        f = self._selected_file()
        if not f:
            QMessageBox.information(self, self.tr(self.tr('Notice')), self.tr(self.tr('Please select a file.')))
            return
        save_path, _ = QFileDialog.getSaveFileName(self, self.tr('Save as'), f['name'])
        if not save_path:
            return
        self.status_label.setText(f'⏳ {self.tr("Downloading")}: {f["name"]} …')
        fm_sig = self._fm_signals
        def worker():
            ser = None
            try:
                remote_path = f"{f['folder']}/{f['name']}"
                ser = self.parent_win._open_connection()
                quicksync.download_file(ser, remote_path, save_path)
                fm_sig.set_status.emit(f'✓ {self.tr("Saved")}: {save_path}')
            except Exception as e:
                fm_sig.set_status.emit(f'✗ {e}')
            finally:
                if ser:
                    quicksync.close_connection(ser)
        threading.Thread(target=worker, daemon=True).start()

    def upload_file(self):
        path, _ = QFileDialog.getOpenFileName(self, self.tr('Choose file to upload'), os.path.expanduser('~'))
        if not path:
            return
        self.status_label.setText(f'⏳ {self.tr("Uploading")}: {os.path.basename(path)} …')
        fm_sig = self._fm_signals
        def worker():
            ser = None
            try:
                ser = self.parent_win._open_connection()
                quicksync.upload_file(ser, os.path.basename(path), path)
                fm_sig.set_status.emit(self.tr('✓ Upload complete'))
                fm_sig.do_reload.emit()
            except Exception as e:
                fm_sig.set_status.emit(f'✗ {e}')
            finally:
                if ser:
                    quicksync.close_connection(ser)
        threading.Thread(target=worker, daemon=True).start()

    def delete_selected(self):
        f = self._selected_file()
        if not f:
            QMessageBox.information(self, self.tr('Notice'), self.tr('Please select a file.'))
            return
        r = QMessageBox.question(self, self.tr('Delete'), self.tr('Really delete file "{}"?').format(f['name']))
        if r != QMessageBox.Yes:
            return
        self.status_label.setText(f'⏳ {self.tr("Deleting")}: {f["name"]} …')
        fm_sig = self._fm_signals
        fm_sig = self._fm_signals
        def worker():
            ser = None
            try:
                remote_path = f"{f['folder']}/{f['name']}"
                ser = self.parent_win._open_connection()
                quicksync.delete_file(ser, remote_path)
                fm_sig.set_status.emit(f'✓ {self.tr("Deleted")}: {f["name"]}')
                fm_sig.do_reload.emit()
            except Exception as e:
                fm_sig.set_status.emit(f'✗ {e}')
            finally:
                if ser:
                    quicksync.close_connection(ser)
        threading.Thread(target=worker, daemon=True).start()

class ContactsWindow(QDialog):
    COLUMNS = [('name', 'Name', 200), ('cell', 'Mobile', 130), ('home', 'Home', 130), ('work', 'Work', 130), ('email', 'E-Mail', 200)]

    def __init__(self, parent: QuickSyncGUI):
        super().__init__(parent)
        self.parent_win = parent
        self.setWindowTitle(self.tr('Manage Contacts'))
        self.resize(900, 500)
        self.cards: list[dict] = []
        self._row_to_index: dict[int, int] = {}
        self.modified_luids: set[str] = set()
        self.deleted_luids: set[str] = set()
        self.temp_vcf_path: str | None = None
        self._reload_thread: threading.Thread | None = None

        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        def tbtn(label, slot):
            b = QPushButton(label); b.clicked.connect(slot); toolbar.addWidget(b); return b
        tbtn(self.tr('New'),          self.new_contact)
        tbtn(self.tr('Edit'),         self.edit_selected)
        tbtn(self.tr('Delete'),       self.delete_selected)
        toolbar.addSpacing(12)
        tbtn(self.tr('Reload'),       self.reload_with_confirm)
        tbtn(self.tr('Save'),         self.save)
        tbtn(self.tr('Transmit'),     self.transmit)
        tbtn(self.tr('Close'),        self._on_close_request)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([c[1] for c in self.COLUMNS])
        for i, (_, _, w) in enumerate(self.COLUMNS): self.table.setColumnWidth(i, w)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(lambda _: self.edit_selected())
        layout.addWidget(self.table, stretch=1)

        self.status_label = QLabel('')
        layout.addWidget(self.status_label)
        self.setModal(False)
        self.show()
        
        # Sofortiger visueller Indikator im Hauptfenster
        self.parent_win.ui_kontakt_anzahl.setText(self.tr("loading…"))
        QTimer.singleShot(50, self.reload)

    def _refresh_status(self):
        pending = sum(1 for c in self.cards if not c.get('luid')) + len(self.modified_luids) + len(self.deleted_luids)
        suffix = f' — {pending} ' + self.tr('unsaved change(s)') if pending else ''
        self.status_label.setText(f'{len(self.cards)} ' + self.tr('contact(s)') + suffix)
        
        # Absolut direkte und sichere Aktualisierung des Hauptfenster-Labels aus dem UI-Thread
        self.parent_win.ui_kontakt_anzahl.setText(str(len(self.cards)))

    def reload(self):
        if self._reload_thread and self._reload_thread.is_alive():
            self.parent_win._signals.append_text.emit(f'⚠ {self.tr("Reload already in progress — please wait.")}')
            return
        self.parent_win.output.clear()
        self.status_label.setText(self.tr('Loading contacts …'))
        sig = self.parent_win._signals

        def worker():
            if not self.parent_win.current_device():
                QTimer.singleShot(0, self, lambda: self._on_error(self.tr('Load failed'), RuntimeError(self.tr('No device connected.'))))
                return
            ser = None
            fd, path = tempfile.mkstemp(suffix='.vcf', prefix='quicksync_')
            os.close(fd)
            try:
                ser = self.parent_win._open_connection()
                vcf_bytes = quicksync.get_contacts(ser)
                with open(path, 'wb') as f:
                    f.write(vcf_bytes)
                vcf = vcf_bytes.decode('utf-8', errors='replace')
                if not vcf.strip():
                    raise RuntimeError(self.tr('Device returned an empty response.'))
                cards = vcard.parseCards(vcf)
                sig.append_text.emit(self.tr('{} contact(s) processed').format(len(cards)))
                QTimer.singleShot(0, self.parent_win, lambda: self.parent_win.ui_kontakt_anzahl.setText(str(len(cards))))
                QTimer.singleShot(0, self, lambda: self._populate(cards, path))
            except Exception as e:
                try:
                    os.unlink(path)
                except OSError:
                    pass
                QTimer.singleShot(0, self, lambda: self._on_error(self.tr('Load failed'), e))
            finally:
                if ser:
                    quicksync.close_connection(ser)

        self._reload_thread = threading.Thread(target=worker, daemon=True)
        self._reload_thread.start()

    def reload_with_confirm(self):
        pending = sum(1 for c in self.cards if not c.get('luid')) + len(self.modified_luids) + len(self.deleted_luids)
        if pending > 0:
            r = QMessageBox.question(self, self.tr('Reload'), self.tr('There are unsaved changes. Reload anyway?'))
            if r != QMessageBox.Yes:
                return
        self.modified_luids.clear()
        self.deleted_luids.clear()
        self.reload()

    def _on_error(self, title, exc):
        self.status_label.setText('')
        QMessageBox.critical(self, title, str(exc))

    def _populate(self, cards, temp_path=None):
        self.temp_vcf_path = temp_path
        self.cards = cards
        self._rebuild_table()

    def _rebuild_table(self):
        self.table.setRowCount(0)
        self._row_to_index = {}
        for i, c in enumerate(self.cards):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._row_to_index[row] = i
            values = [
                vcard.displayName(c),
                c.get('tels', {}).get('CELL', ''),
                c.get('tels', {}).get('HOME', ''),
                c.get('tels', {}).get('WORK', ''),
                (c.get('emails', {}).get('HOME', '') or c.get('emails', {}).get('WORK', '') or c.get('emails', {}).get('OTHER', '')),
            ]
            for col, val in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(val))
        self._refresh_status()

    def new_contact(self):
        blank = {'luid': None, 'last_name': '', 'first_name': '', 'middle_name': '', 'prefix': '', 'suffix': '', 'nickname': '', 'org': '', 'title': '', 'bday': '', 'note': '', 'url': '', 'tels': {'HOME': '', 'CELL': '', 'WORK': '', 'FAX': '', 'OTHER': ''}, 'emails': {'HOME': '', 'WORK': '', 'OTHER': ''}, 'addresses': {'HOME': {'pobox': '', 'ext': '', 'street': '', 'city': '', 'region': '', 'zip': '', 'country': ''}, 'WORK': {'pobox': '', 'ext': '', 'street': '', 'city': '', 'region': '', 'zip': '', 'country': ''}}, 'extras': []}
        ContactEditor(self, blank, on_save=lambda c: (self.cards.append(c), self._rebuild_table()))

    def edit_selected(self):
        rows = self.table.selectedItems()
        if not rows: return
        idx = self._row_to_index.get(self.table.currentRow())
        if idx is not None:
            ContactEditor(self, self.cards[idx], on_save=lambda c: (self.modified_luids.add(c['luid']) if c.get('luid') else None, self._rebuild_table()))

    def delete_selected(self):
        rows = self.table.selectedItems()
        if not rows: return
        idx = self._row_to_index.get(self.table.currentRow())
        if idx is not None:
            luid = self.cards[idx].get('luid')
            if luid: self.deleted_luids.add(luid)
            del self.cards[idx]
            self._rebuild_table()

    def save(self):
        if not self.temp_vcf_path: return
        with open(self.temp_vcf_path, 'w', encoding='utf-8') as f:
            for c in self.cards: f.write(vcard.formatCard(c))

    def transmit(self):
        deletes = list(self.deleted_luids)
        creates = [c for c in self.cards if not c.get('luid')]
        
        def worker():
            ser = None
            try:
                ser = self.parent_win._open_connection()
                for luid in deletes:
                    quicksync.delete_contact(ser, luid)
                    quicksync.close_connection(ser)
                    ser = self.parent_win._open_connection()
                if creates:
                    for c in creates:
                        vcf_bytes = vcard.formatCard(c).encode('utf-8')
                        quicksync.create_contact(ser, vcf_bytes)
                        quicksync.close_connection(ser)
                        ser = self.parent_win._open_connection()
                QTimer.singleShot(0, self.reload)
            except Exception as e:
                QTimer.singleShot(0, lambda: QMessageBox.critical(self, self.tr('Error'), str(e)))
            finally:
                if ser:
                    quicksync.close_connection(ser)
        threading.Thread(target=worker, daemon=True).start()

    def _on_close_request(self): self.close()

class ContactEditor(QDialog):
    def __init__(self, parent, card, on_save):
        super().__init__(parent)
        self.setWindowTitle(self.tr('Edit Contact') if card.get('luid') else self.tr('New Contact'))
        self.resize(400, 450)
        self.card = card
        self.on_save = on_save
        self.entries = {}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        
        self.entries['first_name'] = QLineEdit(card.get('first_name', ''))
        self.entries['last_name'] = QLineEdit(card.get('last_name', ''))
        self.entries['cell'] = QLineEdit(card.get('tels', {}).get('CELL', ''))
        self.entries['home'] = QLineEdit(card.get('tels', {}).get('HOME', ''))
        self.entries['email'] = QLineEdit(card.get('emails', {}).get('HOME', ''))
        
        form.addRow(self.tr('First name') + ':', self.entries['first_name'])
        form.addRow(self.tr('Last name') + ':', self.entries['last_name'])
        form.addRow(self.tr('Mobile') + ':', self.entries['cell'])
        form.addRow(self.tr('Phone') + ':', self.entries['home'])
        form.addRow(self.tr('E-Mail') + ':', self.entries['email'])
        layout.addLayout(form)
        
        btns = _make_dialog_buttons(self)
        btns.accepted.connect(self._save); btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self.show()

    def _save(self):
        self.card['first_name'] = self.entries['first_name'].text()
        self.card['last_name'] = self.entries['last_name'].text()
        self.card.setdefault('tels', {})['CELL'] = self.entries['cell'].text()
        self.card.setdefault('tels', {})['HOME'] = self.entries['home'].text()
        self.card.setdefault('emails', {})['HOME'] = self.entries['email'].text()
        self.on_save(self.card)
        self.accept()

def simple_input(parent, title, prompt) -> str | None:
    dlg = QDialog(parent); dlg.setWindowTitle(title)
    l = QVBoxLayout(dlg); l.addWidget(QLabel(prompt)); e = QLineEdit(); l.addWidget(e)
    b = _make_dialog_buttons(dlg); b.accepted.connect(dlg.accept); b.rejected.connect(dlg.reject); l.addWidget(b)
    if dlg.exec() == QDialog.Accepted: return e.text().strip() or None
    return None

def run():
    import sys
    app = QApplication.instance() or QApplication(sys.argv)

    # Load translation: English is the default. A translator is only
    # installed if the user explicitly selected a different language
    # via the Language settings dialog.
    from PySide6.QtCore import QTranslator, QSettings
    _s = QSettings('QuickSync4LinuxGui', 'QuickSync4LinuxGui')
    saved_lang = _s.value('language', '')
    if saved_lang and saved_lang != 'en':
        translator = QTranslator(app)
        lang_dir = os.path.join(os.path.dirname(__file__), 'lang')
        if translator.load(saved_lang, lang_dir):
            app.installTranslator(translator)
    win = QuickSyncGUI()
    win.show()
    app.exec()

if __name__ == '__main__':
    run()
