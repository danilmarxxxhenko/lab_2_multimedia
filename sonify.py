"""
ЛР2, варіант 2 — соніфікація даних: числові показники → MIDI-композиція.

Правила перетворення (кожен показник спочатку нормується до [0, 1]):
    n = (x − min) / (max − min)

    показник для ВИСОТИ    → номер ступеня гами на 2 октави вгору від C4,
                              тобто нота завжди належить обраній гамі
    показник для ГУЧНОСТІ  → velocity = 40 + n · 80          (40…120)
    показник для ТРИВАЛОСТІ→ одна з тривалостей 0.5, 1, 1.5, 2 долі
                              (якщо не обрано — кожна нота 1 доля)

Кожен рядок даних = одна нота мелодії (доріжка 1).
Доріжка 2 (бас) дублює висоту мелодії на дві октави нижче з фіксованою
гучністю — так мелодія звучить повніше і зберігає ту саму «форму» даних.

Можна запускати й без інтерфейсу:
    python sonify.py data/kyiv_climate.csv --pitch temperature_C --velocity precipitation_mm
"""
import argparse
import csv
import os
import wave
from dataclasses import dataclass

import numpy as np
import mido

TPB = 480                     # тіків MIDI на одну долю (четвертну ноту)

SCALES = {
    "Мажор": [0, 2, 4, 5, 7, 9, 11],
    "Мінор": [0, 2, 3, 5, 7, 8, 10],
    "Пентатоніка": [0, 2, 4, 7, 9],
    "Хроматична": list(range(12)),
}
INSTRUMENTS = {               # назва → номер програми General MIDI
    "Фортепіано": 0,
    "Вібрафон": 11,
    "Струнні": 48,
    "Флейта": 73,
    "Синт. лід": 80,
}
BASS_PROGRAM = 32             # Acoustic Bass
DURATIONS = [0.5, 1.0, 1.5, 2.0]
VEL_MIN, VEL_MAX = 40, 120
ROOT = 60                     # C4
OCTAVES = 2
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


@dataclass
class Note:
    start: float      # початок, долі
    dur: float        # тривалість, долі
    pitch: int        # номер MIDI-ноти
    velocity: int     # 1…127
    track: int        # 0 — мелодія, 1 — бас
    row: int          # номер рядка даних


def note_name(m: int) -> str:
    return f"{NOTE_NAMES[m % 12]}{m // 12 - 1}"


# ---------------------------------------------------------------- дані ----
def load_csv(path: str):
    """Повертає (підписи рядків, {назва_стовпця: масив значень}).
    Числові стовпці визначаються автоматично; перший нечисловий — підписи."""
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    header, data = rows[0], [r for r in rows[1:] if any(c.strip() for c in r)]
    labels, columns = None, {}
    for j, name in enumerate(header):
        vals = [r[j].strip().replace(",", ".") for r in data]
        try:
            columns[name] = np.array([float(v) for v in vals])
        except ValueError:
            if labels is None:
                labels = vals
    if labels is None:
        labels = [str(i + 1) for i in range(len(data))]
    if len(columns) < 2:
        raise ValueError("Потрібно щонайменше два числові стовпці")
    return labels, columns


def normalize(x: np.ndarray) -> np.ndarray:
    lo, hi = float(np.min(x)), float(np.max(x))
    if hi == lo:
        return np.full_like(x, 0.5, dtype=float)
    return (x - lo) / (hi - lo)


# ------------------------------------------------------ правила мапінгу ----
def value_to_pitch(n: float, scale: list) -> int:
    """n ∈ [0,1] → ступінь гами → номер MIDI-ноти (квантування до гами)."""
    steps = len(scale) * OCTAVES          # ступенів у діапазоні (+ верхня тоніка)
    idx = int(round(n * steps))
    octave, degree = divmod(idx, len(scale))
    return ROOT + 12 * octave + scale[degree]


def value_to_velocity(n: float) -> int:
    return int(round(VEL_MIN + n * (VEL_MAX - VEL_MIN)))


def value_to_duration(n: float) -> float:
    return DURATIONS[int(round(n * (len(DURATIONS) - 1)))]


def build_notes(columns: dict, pitch_col: str, vel_col: str, dur_col: str | None,
                scale_name: str = "Мажор", bass: bool = True) -> list:
    scale = SCALES[scale_name]
    np_ = normalize(columns[pitch_col])
    nv = normalize(columns[vel_col])
    nd = normalize(columns[dur_col]) if dur_col else None
    notes, t = [], 0.0
    for i in range(len(np_)):
        pitch = value_to_pitch(np_[i], scale)
        vel = value_to_velocity(nv[i])
        dur = value_to_duration(nd[i]) if nd is not None else 1.0
        notes.append(Note(t, dur, pitch, vel, 0, i))
        if bass:
            notes.append(Note(t, dur, pitch - 24, 70, 1, i))
        t += dur
    return notes


# ------------------------------------------------------- запис MIDI ----
def write_midi(notes: list, path: str, tempo_bpm: int = 100,
               melody_program: int = 0, title: str = "Sonification"):
    """MIDI-файл типу 1: доріжка 0 — темп і назва, 1 — мелодія, 2 — бас."""
    mid = mido.MidiFile(type=1, ticks_per_beat=TPB)

    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("track_name", name=title, time=0))
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(tempo_bpm), time=0))
    meta.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    mid.tracks.append(meta)

    for track_id, (name, program) in enumerate((("Melody", melody_program),
                                                ("Bass", BASS_PROGRAM))):
        tn = [n for n in notes if n.track == track_id]
        if not tn:
            continue
        ch = track_id
        tr = mido.MidiTrack()
        tr.append(mido.MetaMessage("track_name", name=name, time=0))
        tr.append(mido.Message("program_change", channel=ch, program=program, time=0))
        # події з абсолютним часом у тіках → сортування → дельта-час
        events = []
        for n in tn:
            on = int(round(n.start * TPB))
            off = int(round((n.start + n.dur) * TPB)) - 10   # маленька пауза між нотами
            events.append((on, 1, mido.Message("note_on", channel=ch, note=n.pitch, velocity=n.velocity)))
            events.append((off, 0, mido.Message("note_off", channel=ch, note=n.pitch, velocity=0)))
        events.sort(key=lambda e: (e[0], e[1]))           # note_off раніше за note_on
        last = 0
        for tick, _, msg in events:
            tr.append(msg.copy(time=tick - last))
            last = tick
        tr.append(mido.MetaMessage("end_of_track", time=0))
        mid.tracks.append(tr)

    mid.save(path)
    return mid


# ------------------------------------- простий синтезатор для прослуховування ----
def render_wav(notes: list, path: str, tempo_bpm: int = 100, timbre: str = "Фортепіано",
               sr: int = 44100):
    """Перетворює ноти на звук (адитивний синтез з обвідною), щоб прослухати
    результат навіть без MIDI-синтезатора в системі."""
    spb = 60.0 / tempo_bpm
    total = max(n.start + n.dur for n in notes) * spb + 1.0
    out = np.zeros(int(total * sr))
    harmonics = {
        "Фортепіано": [1, 0.5, 0.25, 0.12], "Вібрафон": [1, 0, 0.3, 0, 0.1],
        "Струнні": [1, 0.6, 0.4, 0.3, 0.2, 0.1], "Флейта": [1, 0.15], "Синт. лід": [1, 0.5, 0.33, 0.25, 0.2],
    }.get(timbre, [1, 0.5])
    for n in notes:
        f = 440.0 * 2 ** ((n.pitch - 69) / 12)
        length = int(n.dur * spb * sr)
        t = np.arange(length) / sr
        h = harmonics if n.track == 0 else [1, 0.3]
        sig = sum(a * np.sin(2 * np.pi * f * (k + 1) * t) for k, a in enumerate(h) if a)
        att = min(int(0.01 * sr), length)
        env = np.exp(-t * (2.5 if timbre in ("Фортепіано", "Вібрафон") else 0.6))
        env[:att] *= np.linspace(0, 1, att)
        rel = min(int(0.05 * sr), length)
        env[-rel:] *= np.linspace(1, 0, rel)
        gain = (n.velocity / 127) * (0.5 if n.track == 0 else 0.35)
        s = int(n.start * spb * sr)
        out[s:s + length] += sig * env * gain
    out /= max(1e-9, np.max(np.abs(out))) / 0.9
    pcm = (out * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return len(pcm) / sr


# ------------------------------------------------------------------- CLI ----
def main():
    ap = argparse.ArgumentParser(description="Соніфікація CSV → MIDI")
    ap.add_argument("csv")
    ap.add_argument("--pitch", required=True)
    ap.add_argument("--velocity", required=True)
    ap.add_argument("--duration")
    ap.add_argument("--scale", default="Мажор", choices=SCALES)
    ap.add_argument("--tempo", type=int, default=100)
    ap.add_argument("--instrument", default="Фортепіано", choices=INSTRUMENTS)
    ap.add_argument("-o", "--out", default="output.mid")
    a = ap.parse_args()
    labels, cols = load_csv(a.csv)
    notes = build_notes(cols, a.pitch, a.velocity, a.duration, a.scale)
    write_midi(notes, a.out, a.tempo, INSTRUMENTS[a.instrument], os.path.basename(a.csv))
    for n in notes:
        if n.track == 0:
            print(f"{labels[n.row]:>6}: {note_name(n.pitch):>4} vel={n.velocity:>3} dur={n.dur}")
    print("Збережено:", a.out)


if __name__ == "__main__":
    main()
