import os

_BASE_DIR = os.environ.get("RESOURCEPATH", os.path.dirname(os.path.abspath(__file__)))


def resource_path(relative_path):
    return os.path.join(_BASE_DIR, relative_path)
