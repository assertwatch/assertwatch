"""AssertWatch: catch AI coding agents weakening your tests."""


def __getattr__(name: str) -> str:
    # Looked up lazily: importlib.metadata costs ~30 ms, too much for the hook's fast path.
    if name != "__version__":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("assertwatch")
    except PackageNotFoundError:  # running from a source tree without install
        return "0.0.0+unknown"
