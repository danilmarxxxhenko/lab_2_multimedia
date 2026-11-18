"""
ЛР2, варіант 2 — соніфікація даних. Графічний інтерфейс.

Запуск:
    python app.py                         # приклад: клімат Києва
    python app.py data/demo_signals.csv   # будь-який CSV з ≥ 2 числовими стовпцями

Вікно:
    зверху  — вхідні дані (нормовані до 0…1), вирівняні в часі з нотами
    знизу   — піано-рол згенерованої MIDI-композиції (колір = velocity)
    праворуч— вибір, який показник керує висотою / гучністю / тривалістю,
              гама, інструмент, темп, кнопки
"""
import os
import subprocess
import sys
import time

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import RadioButtons, Slider, Button
from matplotlib import cm, colors

import sonify as S

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")
NONE = "— (1 доля)"
ROLE_COLORS = {"pitch": "tab:blue", "vel": "tab:orange", "dur": "tab:green"}


class Player:
    """Відтворення WAV засобами ОС (Windows — winsound зі стандартної бібліотеки)."""
    def __init__(self):
        self.proc = None

    def play(self, path):
        self.stop()
        if sys.platform.startswith("win"):
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        elif sys.platform == "darwin":
            self.proc = subprocess.Popen(["afplay", path])
        else:
            for cmd in (["paplay", path], ["aplay", "-q", path]):
                try:
                    self.proc = subprocess.Popen(cmd); break
                except FileNotFoundError:
                    continue

    def stop(self):
        if sys.platform.startswith("win"):
            import winsound
            winsound.PlaySound(None, 0)
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None


class App:
    def __init__(self, path):
        self.player = Player()
        self.scale = "Мажор"
        self.instrument = "Фортепіано"
        self.tempo = 100
        self.timer = None
        self.radio_axes = []

        self.fig = plt.figure(figsize=(15.5, 8.8))
        self.fig.canvas.manager.set_window_title("ЛР2 — соніфікація даних: CSV → MIDI")
        self.ax_data = self.fig.add_axes([0.05, 0.60, 0.63, 0.33])
        self.ax_roll = self.fig.add_axes([0.05, 0.08, 0.63, 0.45])
        self._static_controls()
        self.load(path)

    # ---------------------------------------------------------- керування ----
    def _static_controls(self):
        f = self.fig
        f.text(0.86, 0.95, "Гама", weight="bold")
        self.r_scale = RadioButtons(f.add_axes([0.86, 0.80, 0.13, 0.14]), list(S.SCALES), active=0)
        self.r_scale.on_clicked(lambda v: self._set("scale", v))

        f.text(0.86, 0.77, "Інструмент мелодії", weight="bold")
        self.r_instr = RadioButtons(f.add_axes([0.86, 0.59, 0.13, 0.17]), list(S.INSTRUMENTS), active=0)
        self.r_instr.on_clicked(lambda v: self._set("instrument", v))

        f.text(0.86, 0.55, "Темп, BPM", weight="bold")
        self.s_tempo = Slider(f.add_axes([0.87, 0.51, 0.09, 0.025]), "", 60, 200,
                              valinit=self.tempo, valstep=5)
        self.s_tempo.on_changed(lambda v: self._set("tempo", int(v)))

        specs = [("Відкрити CSV…", self.on_open), ("Зберегти MIDI + WAV", self.on_save),
                 ("▶ Відтворити", self.on_play), ("■ Стоп", self.on_stop),
                 ("Відкрити .mid у програвачі", self.on_open_mid)]
        self.buttons = []
        for i, (label, cb) in enumerate(specs):
            b = Button(f.add_axes([0.86, 0.43 - i * 0.055, 0.13, 0.045]), label)
            b.on_clicked(cb)
            self.buttons.append(b)
        self.status = f.text(0.05, 0.965, "", fontsize=10, color="firebrick")
        self.rules = f.text(0.715, 0.015, "", fontsize=8.6, family="monospace", va="bottom")

    def _mapping_controls(self):
        """Перемикачі «показник → параметр MIDI» (перебудовуються для кожного CSV)."""
        for ax in self.radio_axes:
            ax.remove()
        for t in getattr(self, "mapping_titles", []):
            t.remove()
        self.radio_axes, self.mapping_titles = [], []
        names = list(self.columns)
        h = 0.03 * (len(names) + 1)
        y = 0.93
        groups = [("Висота ноти ←", "pitch", names, self.pitch_col),
                  ("Гучність (velocity) ←", "vel", names, self.vel_col),
                  ("Тривалість ←", "dur", names + [NONE], self.dur_col or NONE)]
        self.radios = {}
        for title, role, opts, cur in groups:
            hh = 0.03 * (len(opts) + 1)
            self.mapping_titles.append(self.fig.text(0.715, y + 0.01, title, weight="bold",
                                                     color=ROLE_COLORS[role]))
            ax = self.fig.add_axes([0.715, y - hh, 0.135, hh])
            r = RadioButtons(ax, opts, active=opts.index(cur))
            r.on_clicked(lambda v, role=role: self._set_role(role, v))
            self.radio_axes.append(ax)
            self.radios[role] = r
            y -= hh + 0.05

    def _set(self, attr, value):
        setattr(self, attr, value)
        self.rebuild()

    def _set_role(self, role, value):
        if role == "pitch":
            self.pitch_col = value
        elif role == "vel":
            self.vel_col = value
        else:
            self.dur_col = None if value == NONE else value
        self.rebuild()

    # ------------------------------------------------------------- дані ----
    def load(self, path):
        self.path = path
        self.labels, self.columns = S.load_csv(path)
        names = list(self.columns)
        self.pitch_col, self.vel_col = names[0], names[1]
        self.dur_col = names[2] if len(names) > 2 else None
        self._mapping_controls()
        self.rebuild()

    def rebuild(self):
        self.on_stop(None)
        self.status.set_text("")
        self.notes = S.build_notes(self.columns, self.pitch_col, self.vel_col,
                                   self.dur_col, self.scale)
        self.melody = [n for n in self.notes if n.track == 0]
        self.draw()

    # --------------------------------------------------------- малювання ----
    def draw(self):
        ax = self.ax_data
        ax.clear()
        starts = np.array([n.start for n in self.melody])
        roles = {self.pitch_col: "pitch", self.vel_col: "vel"}
        if self.dur_col:
            roles.setdefault(self.dur_col, "dur")
        for name, x in self.columns.items():
            role = roles.get(name)
            style = dict(color=ROLE_COLORS[role], lw=2.2, marker="o", ms=5) if role \
                else dict(color="lightgray", lw=1, ls="--")
            ax.plot(starts, S.normalize(x), label=f"{name}  [{x.min():g} … {x.max():g}]", **style)
        ax.set_ylim(-0.08, 1.08)
        ax.set_ylabel("нормоване значення")
        ax.set_title(f"Вхідні дані: {os.path.basename(self.path)}  ({len(self.melody)} рядків)", fontsize=11)
        ax.legend(fontsize=8, loc="upper left", ncol=2)
        ax.grid(alpha=0.3)
        step = max(1, len(starts) // 16)
        ax.set_xticks(starts[::step])
        ax.set_xticklabels(self.labels[::step], fontsize=8)
        ax.tick_params(labelbottom=True)
        self.cur_dot, = ax.plot([], [], "o", ms=14, mfc="none", mec="red", mew=2)

        ax = self.ax_roll
        ax.clear()
        norm = colors.Normalize(S.VEL_MIN, S.VEL_MAX)
        cmap = plt.get_cmap("viridis")
        for n in self.notes:
            if n.track == 0:
                ax.broken_barh([(n.start, n.dur * 0.97)], (n.pitch - 0.45, 0.9),
                               color=cmap(norm(n.velocity)), edgecolor="black", lw=0.4)
            else:
                ax.broken_barh([(n.start, n.dur * 0.97)], (n.pitch - 0.45, 0.9), color="silver")
        ps = [n.pitch for n in self.notes]
        lo, hi = min(ps) - 2, max(ps) + 2
        cs = [m for m in range(lo, hi + 1) if m % 12 == 0]
        ax.set_yticks(cs)
        ax.set_yticklabels([S.note_name(m) for m in cs])
        ax.set_ylim(lo, hi)
        end = max(n.start + n.dur for n in self.notes)
        ax.set_xlim(-0.3, end + 0.3)
        self.ax_data.set_xlim(-0.3, end + 0.3)        # спільна шкала часу з даними
        ax.set_xlabel("час, долі (1 доля = четвертна нота)")
        ax.set_ylabel("висота (MIDI)")
        ax.grid(alpha=0.25)
        ax.set_title("Піано-рол: мелодія (колір = velocity) і бас (сірий)", fontsize=11)
        if not hasattr(self, "cbar"):
            self.cbar = self.fig.colorbar(cm.ScalarMappable(norm, cmap), ax=ax, pad=0.01,
                                          fraction=0.03, label="velocity")
        self.playline = ax.axvline(-1, color="red", lw=2)
        self.playline2 = self.ax_data.axvline(-1, color="red", lw=1, alpha=0.6)

        sec = end * 60 / self.tempo
        dur_rule = f"{self.dur_col} → {S.DURATIONS} долі" if self.dur_col else "усі ноти по 1 долі"
        self.rules.set_text(
            "ПРАВИЛА:  n = (x − min)/(max − min)\n"
            f"висота  ← {self.pitch_col}: ступінь гами «{self.scale}»\n"
            f"          C4…C6, idx = round(n·{len(S.SCALES[self.scale]) * S.OCTAVES})\n"
            f"velocity← {self.vel_col}: 40 + n·80\n"
            f"тривал. ← {dur_rule}\n"
            f"бас: та сама нота −2 октави, vel 70\n"
            f"нот: {len(self.melody)}×2, {sec:.1f} с при {self.tempo} BPM")
        self.fig.canvas.draw_idle()

    # ------------------------------------------------------------ кнопки ----
    def _paths(self):
        os.makedirs(OUT, exist_ok=True)
        base = os.path.splitext(os.path.basename(self.path))[0]
        return os.path.join(OUT, base + ".mid"), os.path.join(OUT, base + ".wav")

    def on_save(self, _):
        mid, wav = self._paths()
        S.write_midi(self.notes, mid, self.tempo, S.INSTRUMENTS[self.instrument],
                     os.path.basename(self.path))
        S.render_wav(self.notes, wav, self.tempo, self.instrument)
        self.status.set_text(f"Збережено: output/{os.path.basename(mid)} і .wav")
        self.fig.canvas.draw_idle()
        return mid, wav

    def on_play(self, _):
        mid, wav = self.on_save(None)
        self.player.play(wav)
        self.t0 = time.time()
        self.end = max(n.start + n.dur for n in self.notes)
        self.timer = self.fig.canvas.new_timer(interval=40)
        self.timer.add_callback(self._tick)
        self.timer.start()

    def _tick(self):
        beat = (time.time() - self.t0) * self.tempo / 60
        if beat > self.end:
            self.on_stop(None)
            return
        for ln in (self.playline, self.playline2):
            ln.set_xdata([beat, beat])
        cur = [n for n in self.melody if n.start <= beat < n.start + n.dur]
        if cur:
            n = cur[0]
            y = S.normalize(self.columns[self.pitch_col])[n.row]
            self.cur_dot.set_data([n.start], [y])
            self.status.set_text(f"{self.labels[n.row]}: {self.pitch_col}={self.columns[self.pitch_col][n.row]:g}"
                                 f" → {S.note_name(n.pitch)}, vel={n.velocity}, {n.dur} долі")
        self.fig.canvas.draw_idle()

    def on_stop(self, _):
        if self.timer:
            self.timer.stop()
            self.timer = None
        self.player.stop()
        if hasattr(self, "playline"):
            for ln in (self.playline, self.playline2):
                ln.set_xdata([-1, -1])
            self.cur_dot.set_data([], [])
            self.fig.canvas.draw_idle()

    def on_open_mid(self, _):
        mid, _ = self.on_save(None)
        if sys.platform.startswith("win"):
            os.startfile(mid)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", mid])
        else:
            subprocess.Popen(["xdg-open", mid])

    def on_open(self, _):
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk(); root.withdraw()
            path = filedialog.askopenfilename(initialdir=os.path.join(HERE, "data"),
                                              filetypes=[("CSV", "*.csv")])
            root.destroy()
        except Exception as e:
            print("Діалог недоступний:", e)
            return
        if path:
            try:
                self.load(path)
            except Exception as e:
                self.status.set_text(f"Помилка: {e}")
                self.fig.canvas.draw_idle()


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "data", "kyiv_climate.csv")
    App(path)
    plt.show()


if __name__ == "__main__":
    main()
