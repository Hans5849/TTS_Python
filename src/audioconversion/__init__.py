"""Deprecated namespace; use tts_python. Compatibility retained for one migration cycle."""
import importlib
import pkgutil
import sys
import tts_python as _canonical
for _item in pkgutil.walk_packages(_canonical.__path__, _canonical.__name__ + "."):
    _module = importlib.import_module(_item.name)
    sys.modules[__name__ + _item.name[len(_canonical.__name__):]] = _module
