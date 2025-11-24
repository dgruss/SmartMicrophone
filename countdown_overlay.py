#!/usr/bin/env python3
"""Fullscreen countdown overlay rendered locally with transparent background support."""

from __future__ import annotations

import sys

tk = None
tkfont = None
_tk_error = None
try:  # pragma: no cover - handled at runtime when Tk isn't available
    import tkinter as tk
    import tkinter.font as tkfont
except Exception as exc:  # pragma: no cover
    _tk_error = exc


BACKGROUND_COLOR = '#010101'  # Near-black key color to mark transparent regions
FOREGROUND_COLOR = '#FFFFFF'


class TransparencyUnsupported(Exception):
    """Raised when the current backend can't provide a transparent background."""


def parse_seconds(argv):
    try:
        return max(1, int(float(argv[1])))
    except Exception:
        return 15


def run_tk_overlay(duration, require_transparency=True):
    """Show a fullscreen Tk overlay. Optionally require transparency support."""

    if tk is None:
        if _tk_error:
            raise RuntimeError(f'Tkinter unavailable: {_tk_error}')
        raise RuntimeError('Tkinter is not available in this environment.')

    root = tk.Tk()
    root.title('Playlist Countdown')
    root.configure(bg=BACKGROUND_COLOR)
    root.attributes('-fullscreen', True)
    root.attributes('-topmost', True)
    root.configure(cursor='none')
    root.overrideredirect(True)

    transparency_supported = False
    try:
        root.wm_attributes('-transparentcolor', BACKGROUND_COLOR)
        transparency_supported = True
        print('Using Tk overlay with transparent background support.')
    except tk.TclError:
        if require_transparency:
            root.destroy()
            raise TransparencyUnsupported('Tk transparentcolor unsupported on this compositor.')
        # Fall back to a slightly translucent overlay if transparency isn't mandatory.
        try:
            root.attributes('-alpha', 0.15)
        except tk.TclError:
            pass
        print('Using Tk overlay with slight opacity (transparency not supported).')

    root.update_idletasks()
    screen_w = root.winfo_screenwidth() or 1920
    screen_h = root.winfo_screenheight() or 1080
    root.geometry(f"{screen_w}x{screen_h}+0+0")
    font_size = max(180, int(min(screen_w, screen_h) * 0.55))
    count_font = tkfont.Font(family='Helvetica', size=font_size, weight='bold')

    count_var = tk.StringVar(value=str(duration))

    canvas = tk.Canvas(
        root,
        width=screen_w,
        height=screen_h,
        bg=BACKGROUND_COLOR,
        highlightthickness=0,
        borderwidth=0
    )
    canvas.pack(fill='both', expand=True)

    # Use a color with 50% opacity for the countdown number (white, alpha=128)
    # Tkinter does not support alpha in text directly, so we use a workaround:
    # Draw text twice, once in white, once in transparent white (simulate 50% opacity)
    # But the best we can do is use a color like #FFFFFF80 if supported, else fallback to white
    try:
        text_color = '#FFFFFF80'  # RGBA hex, 50% opacity if supported
        text_item = canvas.create_text(
            screen_w / 2,
            screen_h / 2,
            text=count_var.get(),
            fill=text_color,
            font=count_font,
            anchor='center'
        )
    except Exception:
        # Fallback: just use white
        text_item = canvas.create_text(
            screen_w / 2,
            screen_h / 2,
            text=count_var.get(),
            fill=FOREGROUND_COLOR,
            font=count_font,
            anchor='center'
        )

    def close_overlay(event=None):
        root.destroy()

    def tick(remaining):
        if remaining <= 0:
            close_overlay()
            return
        count_var.set(str(remaining))
        canvas.itemconfig(text_item, text=count_var.get())
        root.after(1000, lambda: tick(remaining - 1))

    root.bind('<Escape>', close_overlay)
    root.after(0, lambda: tick(duration))

    try:
        root.mainloop()
    except KeyboardInterrupt:  # pragma: no cover - manual interruption
        close_overlay()

    return transparency_supported or not require_transparency


def run_qt_overlay(duration):
    """Try to show the countdown using PyQt with a translucent window."""

    try:
        from PyQt5 import QtCore, QtGui, QtWidgets
    except ImportError as exc:
        print(f'PyQt5 not available for transparent overlay fallback: {exc}')
        return False

    class CountdownWindow(QtWidgets.QWidget):
        def __init__(self, seconds):
            super().__init__()
            self.remaining = seconds
            self.setWindowFlags(
                QtCore.Qt.FramelessWindowHint
                | QtCore.Qt.WindowStaysOnTopHint
                | QtCore.Qt.Window
            )
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
            self.setAttribute(QtCore.Qt.WA_NoSystemBackground)
            self.setCursor(QtCore.Qt.BlankCursor)

            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)

            self.label = QtWidgets.QLabel(str(seconds))
            self.label.setAlignment(QtCore.Qt.AlignCenter)

            screen = QtWidgets.QApplication.primaryScreen()
            screen_size = screen.size() if screen else QtCore.QSize(1920, 1080)
            font_size = max(180, int(min(screen_size.width(), screen_size.height()) * 0.55))
            font = QtGui.QFont('Helvetica', font_size)
            font.setBold(True)
            self.label.setFont(font)
            # 50% opacity white text, fully transparent background
            self.label.setStyleSheet('color: rgba(255,255,255,0.5); background-color: rgba(0,0,0,0);')

            layout.addWidget(self.label)

            self.timer = QtCore.QTimer(self)
            self.timer.timeout.connect(self._tick)
            self.timer.start(1000)

        def _tick(self):
            self.remaining -= 1
            if self.remaining <= 0:
                self.timer.stop()
                self.close()
                return
            self.label.setText(str(self.remaining))

        def keyPressEvent(self, event):  # noqa: N802 - Qt signature
            if event.key() == QtCore.Qt.Key_Escape:
                event.accept()
                self.close()
            else:  # pragma: no cover - passthrough for other keys
                super().keyPressEvent(event)

    print('Using PyQt transparent overlay backend.')
    app = QtWidgets.QApplication(sys.argv[:1])
    window = CountdownWindow(duration)
    window.showFullScreen()
    try:
        app.exec_()
    except KeyboardInterrupt:  # pragma: no cover
        window.close()
    return True


def main():
    import logging
    logging.basicConfig(filename='virtual-microphone.log', level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    duration = parse_seconds(sys.argv)
    logging.info(f'Launching countdown overlay for {duration}s')

    if tk is not None:
        try:
            run_tk_overlay(duration, require_transparency=True)
            logging.info('Tk overlay launched successfully (transparent)')
            return
        except TransparencyUnsupported as exc:
            logging.warning(f'Tk overlay cannot become transparent: {exc}. Trying PyQt fallback...')
        except Exception as exc:  # pragma: no cover - unexpected Tk issues
            logging.error(f'Tk overlay failed unexpectedly ({exc}); attempting PyQt fallback...')

    if run_qt_overlay(duration):
        logging.info('PyQt overlay launched successfully (transparent)')
        return

    if tk is not None:
        logging.warning('Transparent backend unavailable; showing semi-opaque Tk overlay instead.')
        run_tk_overlay(duration, require_transparency=False)
        logging.info('Tk overlay launched with semi-opaque background')
        return

    logging.error('Unable to present countdown overlay. Install PyQt5 or ensure Tk supports transparent windows.')


if __name__ == '__main__':
    main()
