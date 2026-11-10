from pathlib import Path
from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProcessingProvider
from .algorithm import DEMMapSheetsAlgorithm


class DEMMapSheetsProvider(QgsProcessingProvider):
    def id(self): return 'demmapsheets'
    def name(self): return 'DEM Map Sheets'
    def longName(self): return 'DEM Map Sheets — DEM作成・マージ・TerrainRGB XYZ'
    def icon(self): return QIcon(str(Path(__file__).with_name('icon.svg')))
    def loadAlgorithms(self): self.addAlgorithm(DEMMapSheetsAlgorithm())
