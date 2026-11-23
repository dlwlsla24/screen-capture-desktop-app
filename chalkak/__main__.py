import sys


def main():
    if sys.platform == "win32":
        from .win.app import main as launch
    else:
        from .app import main as launch
    return launch()


if __name__ == "__main__":
    sys.exit(main())
