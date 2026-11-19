#!/usr/bin/env python3
import sys
import os
import pty
import fcntl
import termios
import struct
import signal
import codecs
from PyQt6.QtWidgets import QApplication, QMainWindow, QPlainTextEdit
from PyQt6.QtCore import QObject, QSocketNotifier, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics, QColor, QTextCharFormat, QTextCursor

ESC = chr(27)
BEL = chr(7)
CR = chr(13)
LF = chr(10)
BS = chr(8)
TAB = chr(9)
DEL = chr(127)

SEQ_UP = bytes([27]) + b'[A'
SEQ_DOWN = bytes([27]) + b'[B'
SEQ_RIGHT = bytes([27]) + b'[C'
SEQ_LEFT = bytes([27]) + b'[D'
SEQ_HOME = bytes([27]) + b'[H'
SEQ_END = bytes([27]) + b'[F'
SEQ_DEL = bytes([27]) + b'[3~'
SEQ_PGUP = bytes([27]) + b'[5~'
SEQ_PGDN = bytes([27]) + b'[6~'
SEQ_INS = bytes([27]) + b'[2~'

DEFAULT_STYLE = (None, None, False, False)

PALETTE16 = [(0,0,0),(170,0,0),(0,170,0),(170,85,0),(0,0,170),(170,0,170),(0,170,170),(170,170,170),(85,85,85),(255,85,85),(85,255,85),(255,255,85),(85,85,255),(255,85,255),(85,255,255),(255,255,255)]

def color_for(c):
    if c is None:
        return None
    if isinstance(c, tuple):
        return QColor(c[0], c[1], c[2])
    if c < 16:
        r, g, b = PALETTE16[c]
        return QColor(r, g, b)
    if c < 232:
        n = c - 16
        r = n // 36
        g = (n % 36) // 6
        b = n % 6
        conv = lambda v: 0 if v == 0 else 55 + v * 40
        return QColor(conv(r), conv(g), conv(b))
    v = 8 + (c - 232) * 10
    return QColor(v, v, v)


def spawn_pty(argv, env, rows=30, cols=100):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
    pid = os.fork()
    if pid == 0:
        try:
            os.close(master)
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
            os.dup2(slave, 0)
            os.dup2(slave, 1)
            os.dup2(slave, 2)
            if slave > 2:
                os.close(slave)
            os.execvpe(argv[0], argv, env)
        except Exception:
            pass
        os._exit(127)
    os.close(slave)
    return master, pid


class PtyProcess(QObject):
    dataReceived = pyqtSignal(bytes)
    closed = pyqtSignal()

    def __init__(self, master, pid):
        super().__init__()
        self.master = master
        self.pid = pid
        self.alive = True
        flags = fcntl.fcntl(master, fcntl.F_GETFL)
        fcntl.fcntl(master, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self.notifier = QSocketNotifier(master, QSocketNotifier.Type.Read, self)
        self.notifier.activated.connect(self._on_readable)

    def _on_readable(self):
        try:
            data = os.read(self.master, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b''
        if not data:
            self.notifier.setEnabled(False)
            if self.alive:
                self.alive = False
                self.closed.emit()
            return
        self.dataReceived.emit(data)

    def write(self, data):
        if not self.alive:
            return
        try:
            os.write(self.master, data)
        except OSError:
            pass

    def resize(self, rows, cols):
        try:
            fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
        except OSError:
            pass

    def terminate(self):
        if self.alive and self.pid > 0:
            self.alive = False
            try:
                os.killpg(os.getpgid(self.pid), signal.SIGHUP)
            except OSError:
                pass


class Screen:
    def __init__(self, rows=30, cols=100, scrollback=5000):
        self.rows = rows
        self.cols = cols
        self.scrollback = scrollback
        self.lines = [[]]
        self.row = 0
        self.col = 0
        self.fg = None
        self.bg = None
        self.bold = False
        self.underline = False
        self.state = 'normal'
        self.buf = ''

    def style(self):
        return (self.fg, self.bg, self.bold, self.underline)

    def ensure(self, row):
        while len(self.lines) <= row:
            self.lines.append([])

    def put(self, ch):
        if self.col >= self.cols:
            self.col = 0
            self.newline()
        self.ensure(self.row)
        line = self.lines[self.row]
        while len(line) < self.col:
            line.append((' ', DEFAULT_STYLE))
        cell = (ch, self.style())
        if self.col < len(line):
            line[self.col] = cell
        else:
            line.append(cell)
        self.col += 1

    def newline(self):
        self.row += 1
        self.ensure(self.row)
        limit = self.scrollback + self.rows
        if len(self.lines) > limit:
            drop = len(self.lines) - limit
            self.lines = self.lines[drop:]
            self.row = max(0, self.row - drop)

    def feed(self, text):
        for ch in text:
            self._feed_char(ch)

    def _feed_char(self, ch):
        st = self.state
        if st == 'normal':
            if ch == ESC:
                self.state = 'esc'
            elif ch == LF:
                self.newline()
            elif ch == CR:
                self.col = 0
            elif ch == BS:
                self.col = max(0, self.col - 1)
            elif ch == TAB:
                self.col = min(self.cols - 1, (self.col // 8 + 1) * 8)
            elif ch == BEL:
                pass
            elif ch == chr(0x9b):
                self.state = 'csi'
                self.buf = ''
            elif ord(ch) < 32:
                pass
            else:
                self.put(ch)
        elif st == 'esc':
            self.state = 'normal'
            if ch == '[':
                self.state = 'csi'
                self.buf = ''
            elif ch == ']' or ch == 'P':
                self.state = 'osc'
                self.buf = ''
            elif ch in '()*+-./#':
                self.state = 'charset'
            elif ch == 'M':
                if self.row > 0:
                    self.row -= 1
            elif ch == 'D':
                self.newline()
            elif ch == 'E':
                self.col = 0
                self.newline()
            elif ch == ESC:
                self.state = 'esc'
        elif st == 'charset':
            self.state = 'normal'
        elif st == 'csi':
            if ch == ESC:
                self.state = 'esc'
                return
            self.buf += ch
            if ch >= '@' and ch <= '~':
                params = self.buf[:-1]
                self.buf = ''
                self.state = 'normal'
                self.handle_csi(params, ch)
        elif st == 'osc':
            if ch == BEL:
                self.state = 'normal'
                self.buf = ''
            elif ch == ESC:
                self.state = 'osc_esc'
            else:
                self.buf += ch
        elif st == 'osc_esc':
            self.state = 'normal'
            self.buf = ''

    def handle_csi(self, params, final):
        try:
            self._handle_csi(params, final)
        except Exception:
            pass

    def _handle_csi(self, params, final):
        if final == 'm':
            self.sgr(params)
            return
        if final == 'J':
            n = params or '0'
            if n in ('2', '3'):
                self.lines = [[]]
                self.row = 0
                self.col = 0
            elif n == '0':
                self.ensure(self.row)
                del self.lines[self.row][self.col:]
                del self.lines[self.row + 1:]
            elif n == '1':
                for r in range(self.row):
                    self.lines[r] = []
                line = self.lines[self.row]
                for c in range(min(self.col + 1, len(line))):
                    line[c] = (' ', DEFAULT_STYLE)
            return
        if final == 'K':
            self.ensure(self.row)
            line = self.lines[self.row]
            n = params or '0'
            if n == '0':
                del line[self.col:]
            elif n == '1':
                for c in range(min(self.col + 1, len(line))):
                    line[c] = (' ', DEFAULT_STYLE)
            elif n == '2':
                self.lines[self.row] = []
            return
        if final == 'H' or final == 'f':
            parts = (params or '1;1').split(';')
            r = int(parts[0] or 1) - 1
            c = int(parts[1] or 1) - 1 if len(parts) > 1 else 0
            self.row = max(0, r)
            self.col = max(0, c)
            self.ensure(self.row)
            return
        if final == 'A':
            self.row = max(0, self.row - int(params or 1))
        elif final == 'B':
            self.row += int(params or 1)
            self.ensure(self.row)
        elif final == 'C':
            self.col = min(self.cols - 1, self.col + int(params or 1))
        elif final == 'D':
            self.col = max(0, self.col - int(params or 1))
        elif final == 'G':
            self.col = max(0, int(params or 1) - 1)
        elif final == 'd':
            self.row = max(0, int(params or 1) - 1)
            self.ensure(self.row)
        elif final == 'X':
            self.ensure(self.row)
            line = self.lines[self.row]
            n = int(params or 1)
            for c in range(self.col, min(len(line), self.col + n)):
                line[c] = (' ', DEFAULT_STYLE)

    def sgr(self, params):
        parts = params.split(';') if params else ['0']
        i = 0
        while i < len(parts):
            p = parts[i] or '0'
            try:
                v = int(p)
            except ValueError:
                v = 0
            if v == 0:
                self.fg = None
                self.bg = None
                self.bold = False
                self.underline = False
            elif v == 1:
                self.bold = True
            elif v == 4:
                self.underline = True
            elif v == 22:
                self.bold = False
            elif v == 24:
                self.underline = False
            elif 30 <= v <= 37:
                self.fg = v - 30
            elif v == 39:
                self.fg = None
            elif 40 <= v <= 47:
                self.bg = v - 40
            elif v == 49:
                self.bg = None
            elif 90 <= v <= 97:
                self.fg = v - 90 + 8
            elif 100 <= v <= 107:
                self.bg = v - 100 + 8
            elif v == 38 or v == 48:
                target = 'fg' if v == 38 else 'bg'
                if i + 1 < len(parts):
                    mode = parts[i + 1]
                    if mode == '5' and i + 2 < len(parts):
                        try:
                            setattr(self, target, int(parts[i + 2]))
                        except ValueError:
                            pass
                        i += 2
                    elif mode == '2' and i + 4 < len(parts):
                        try:
                            setattr(self, target, (int(parts[i + 2]), int(parts[i + 3]), int(parts[i + 4])))
                        except ValueError:
                            pass
                        i += 4
            i += 1

    def to_text(self):
        return LF.join(''.join(c[0] for c in line) for line in self.lines)


class TerminalView(QPlainTextEdit):
    def __init__(self, screen, parent=None):
        super().__init__(parent)
        self.screen = screen
        self.input_handler = None
        self.show_cursor = True
        self._fmt_cache = {}
        self.setReadOnly(True)
        self.setCursorWidth(0)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setStyleSheet('QPlainTextEdit { background: #101014; color: #e6e6e6; border: none; }')
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(30)
        self._render_timer.timeout.connect(self.render)

    def schedule_render(self):
        if not self._render_timer.isActive():
            self._render_timer.start()

    def fmt_for(self, st):
        if st == 'CURSOR':
            f = QTextCharFormat()
            f.setBackground(QColor('#e6e6e6'))
            f.setForeground(QColor('#101014'))
            return f
        f = self._fmt_cache.get(st)
        if f is not None:
            return f
        fg, bg, bold, underline = st
        f = QTextCharFormat()
        c = color_for(fg)
        if c is None:
            c = QColor('#e6e6e6')
        f.setForeground(c)
        if bg is not None:
            bc = color_for(bg)
            if bc is not None:
                f.setBackground(bc)
        f.setFontWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
        f.setFontUnderline(bool(underline))
        self._fmt_cache[st] = f
        return f

    def _emit_line(self, cur, cells):
        n = len(cells)
        i = 0
        while i < n:
            ch, st = cells[i]
            run = [ch]
            j = i + 1
            while j < n and cells[j][1] == st:
                run.append(cells[j][0])
                j += 1
            cur.insertText(''.join(run), self.fmt_for(st))
            i = j

    def render(self):
        sc = self.screen
        doc = self.document()
        doc.clear()
        cur = QTextCursor(doc)
        for r in range(len(sc.lines)):
            line = sc.lines[r]
            if self.show_cursor and r == sc.row:
                cells = list(line)
                col = min(sc.col, sc.cols - 1)
                while len(cells) <= col:
                    cells.append((' ', DEFAULT_STYLE))
                ch = cells[col][0]
                cells[col] = (ch, 'CURSOR')
                self._emit_line(cur, cells)
            else:
                self._emit_line(cur, line)
            cur.insertText(LF)
        QTimer.singleShot(0, self._scroll_bottom)

    def _scroll_bottom(self):
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())

    def keyPressEvent(self, e):
        if self.input_handler is None:
            return
        k = e.key()
        mods = e.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        if ctrl and shift and k == Qt.Key.Key_C:
            self.copy()
            return
        if ctrl and shift and k == Qt.Key.Key_V:
            txt = QApplication.clipboard().text()
            if txt:
                self.input_handler(txt.encode('utf-8'))
            return
        seq = None
        if ctrl and Qt.Key.Key_A <= k <= Qt.Key.Key_Z:
            seq = bytes([k - Qt.Key.Key_A + 1])
        elif k == Qt.Key.Key_Return or k == Qt.Key.Key_Enter:
            seq = bytes([13])
        elif k == Qt.Key.Key_Backspace:
            seq = bytes([127])
        elif k == Qt.Key.Key_Tab:
            seq = bytes([9])
        elif k == Qt.Key.Key_Escape:
            seq = bytes([27])
        elif k == Qt.Key.Key_Up:
            seq = SEQ_UP
        elif k == Qt.Key.Key_Down:
            seq = SEQ_DOWN
        elif k == Qt.Key.Key_Right:
            seq = SEQ_RIGHT
        elif k == Qt.Key.Key_Left:
            seq = SEQ_LEFT
        elif k == Qt.Key.Key_Home:
            seq = SEQ_HOME
        elif k == Qt.Key.Key_End:
            seq = SEQ_END
        elif k == Qt.Key.Key_Delete:
            seq = SEQ_DEL
        elif k == Qt.Key.Key_PageUp:
            seq = SEQ_PGUP
        elif k == Qt.Key.Key_PageDown:
            seq = SEQ_PGDN
        elif k == Qt.Key.Key_Insert:
            seq = SEQ_INS
        else:
            txt = e.text()
            if alt and txt:
                seq = bytes([27]) + txt.encode('utf-8')
            elif txt and ord(txt[0]) >= 32:
                seq = txt.encode('utf-8')
        if seq:
            self.input_handler(seq)


class MainWindow(QMainWindow):
    def __init__(self, master, pid):
        super().__init__()
        self.setWindowTitle('Qt Terminal')
        font = QFont('DejaVu Sans Mono', 11)
        fm = QFontMetrics(font)
        self.adv = max(1, fm.horizontalAdvance('M'))
        self.lh = max(1, fm.lineSpacing())
        self.rows = 30
        self.cols = 100
        self.screen = Screen(self.rows, self.cols)
        self.view = TerminalView(self.screen)
        self.view.setFont(font)
        self.setCentralWidget(self.view)
        self.resize(self.cols * self.adv + 24, self.rows * self.lh + 24)
        self.pty = PtyProcess(master, pid)
        self.decoder = codecs.getincrementaldecoder('utf-8')('replace')
        self.pty.dataReceived.connect(self.on_data)
        self.pty.closed.connect(self.on_closed)
        self.view.input_handler = self.pty.write
        self.view.setFocus()

    def on_data(self, data):
        text = self.decoder.decode(data)
        if text:
            self.screen.feed(text)
            self.view.schedule_render()

    def on_closed(self):
        self.setWindowTitle('Qt Terminal [process exited]')

    def sync_size(self):
        vp = self.view.viewport()
        cols = max(20, vp.width() // self.adv)
        rows = max(5, vp.height() // self.lh)
        if cols != self.screen.cols or rows != self.screen.rows:
            self.screen.cols = cols
            self.screen.rows = rows
            self.pty.resize(rows, cols)
            self.view.schedule_render()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.sync_size()

    def closeEvent(self, e):
        self.pty.terminate()
        super().closeEvent(e)


def main():
    args = sys.argv[1:]
    smoke = '--smoke' in args
    args = [a for a in args if a != '--smoke']
    if args:
        cmd = args
    else:
        shell = os.environ.get('SHELL', '/bin/bash')
        cmd = [shell, '-i']
    env = dict(os.environ)
    env['TERM'] = 'xterm-256color'
    env['COLORTERM'] = 'truecolor'
    master, pid = spawn_pty(cmd, env, 30, 100)
    app = QApplication([sys.argv[0]])
    win = MainWindow(master, pid)
    win.show()
    QTimer.singleShot(0, win.sync_size)
    QTimer.singleShot(0, win.view.setFocus)
    if smoke:
        def send():
            win.pty.write(b'echo SMOKE_OK_42' + bytes([13]))
        def finish():
            print('--- screen ---')
            print(win.screen.to_text())
            print('--- end ---')
            win.pty.terminate()
            app.quit()
        QTimer.singleShot(700, send)
        QTimer.singleShot(2200, finish)
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
