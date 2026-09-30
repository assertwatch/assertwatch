"""AssertWatch: catch AI coding agents weakening your tests."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("assertwatch")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0+unknown"
