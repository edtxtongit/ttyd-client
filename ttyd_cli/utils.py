"""Utility functions for terminal operations."""

import os
import platform
import shutil

IS_WINDOWS = platform.system() == "Windows"


def get_terminal_size(fallback: tuple[int, int] = (80, 24)) -> tuple[int, int]:
    try:
        size = shutil.get_terminal_size(fallback=fallback) if IS_WINDOWS else os.get_terminal_size()
    except OSError:
        return fallback
    return size.columns, size.lines
