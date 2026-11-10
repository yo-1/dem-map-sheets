"""QGIS GUI integration. Copyright (C) 2026 Yoichi Wada. GPL-3.0-only."""
from pathlib import Path
from qgis.PyQt.QtGui import QAction, QIcon
from qgis.PyQt.QtCore import QEvent, QObject, QTimer
from qgis.PyQt.QtWidgets import QApplication, QAbstractSpinBox, QComboBox
from qgis.core import QgsApplication
from .provider import DEMMapSheetsProvider
from .version import VERSION


class _WheelGuard(QObject):
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Wheel and isinstance(obj, (QAbstractSpinBox, QComboBox)):
            return True
        return super().eventFilter(obj, event)


class DEMMapSheetsPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None
        self.wheel_guard = _WheelGuard()

    def initProcessing(self):
        if self.provider is None:
            self.provider = DEMMapSheetsProvider()
            QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()
        self.action = QAction(QIcon(str(Path(__file__).with_name('icon.svg'))),
                              f'DEM Map Sheets v{VERSION}：DEM作成・マージ・XYZタイル', self.iface.mainWindow())
        self.action.triggered.connect(self.open_dialog)
        self.iface.addPluginToRasterMenu('DEM Map Sheets', self.action)
        self.iface.addToolBarIcon(self.action)

    def open_dialog(self):
        from .settings_dialog import SettingsDialog
        def protect_controls():
            dialog = QApplication.activeModalWidget()
            if dialog is None:
                return
            for widget in dialog.findChildren(QAbstractSpinBox) + dialog.findChildren(QComboBox):
                widget.installEventFilter(self.wheel_guard)
                widget.setToolTip((widget.toolTip() + '\n' if widget.toolTip() else '') +
                                  '誤操作防止のためマウスホイールでは変更できません。')
        QTimer.singleShot(0, protect_controls)
        algorithm = QgsApplication.processingRegistry().createAlgorithmById('demmapsheets:create')
        dialog = SettingsDialog(algorithm, self.iface.mainWindow())
        dialog.exec()

    def unload(self):
        if self.action is not None:
            self.iface.removePluginRasterMenu('DEM Map Sheets', self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
