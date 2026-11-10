"""Read the installed version from the distribution metadata."""
from configparser import ConfigParser
from pathlib import Path

_metadata = ConfigParser(interpolation=None)
_metadata.read(Path(__file__).with_name('metadata.txt'), encoding='utf-8')
VERSION = _metadata.get('general', 'version')
