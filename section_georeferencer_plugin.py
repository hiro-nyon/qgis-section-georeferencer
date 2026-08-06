"""QGIS plugin boilerplate for Section Georeferencer."""

from __future__ import annotations

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .section_georeferencer_dock import SectionGeoreferencerDock

_PLUGIN_DIR = os.path.dirname(__file__)
_ICON_PATH = os.path.join(_PLUGIN_DIR, "icon.svg")


class SectionGeoreferencerPlugin:
    """Main QGIS plugin class."""

    MENU_TEXT = "&Section Georeferencer"

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dock = None

    def initGui(self):
        icon = QIcon(_ICON_PATH)
        self.action = QAction(icon, "Section Georeferencer", self.iface.mainWindow())
        self.action.setObjectName("section_georeferencer_action")
        self.action.setCheckable(True)
        self.action.triggered.connect(self.toggle_dock)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu(self.MENU_TEXT, self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removePluginMenu(self.MENU_TEXT, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None
        if self.dock is not None:
            self.dock.cleanup()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None

    def toggle_dock(self, checked=False):
        if self.dock is None:
            self.dock = SectionGeoreferencerDock(self.iface)
            self.dock.setWindowIcon(QIcon(_ICON_PATH))
            self.dock.visibilityChanged.connect(self._dock_visibility_changed)
            self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.setVisible(not self.dock.isVisible() if not checked else True)
        self.dock.raise_()

    def _dock_visibility_changed(self, visible):
        if self.action is not None:
            self.action.setChecked(bool(visible))
        if self.dock is not None:
            self.dock.set_orientation_guide_visible(visible)
