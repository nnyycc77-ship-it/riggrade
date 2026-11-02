"""RigGrade: independent GPU rig grading. See docs/grading-spec.md."""

BENCH_VERSION = "v0.1"

try:  # the installed distribution's own version, so a notebook can print what pip gave it
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _version
    __version__ = _version("riggrade")
except PackageNotFoundError:  # running from a source checkout
    __version__ = "source"
