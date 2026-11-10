"""QGIS plugin entry point. Copyright (C) 2026 yo-1. GPL-3.0-only."""

def classFactory(iface):
    from .plugin import DEMMapSheetsPlugin
    return DEMMapSheetsPlugin(iface)
