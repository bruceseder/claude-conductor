# DPI awareness MUST be set before tkinter import
from widget.utils import setup_dpi_awareness
setup_dpi_awareness()

from widget.app import App


def main():
    app = App()
    app.run()


if __name__ == '__main__':
    main()
