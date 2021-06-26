import sys

# https://pyinstaller.readthedocs.io/en/stable/runtime-information.html
def is_pyinstaller():
    return getattr(sys, 'frozen', False)
