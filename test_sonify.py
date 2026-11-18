"""Перевірка правил перетворення та коректності MIDI-файлу.  python test_sonify.py"""
import os, tempfile
import mido
import sonify as S

labels, cols = S.load_csv(os.path.join(os.path.dirname(__file__), "data", "kyiv_climate.csv"))
notes = S.build_notes(cols, "temperature_C", "precipitation_mm", None, "Мажор")
mel = [n for n in notes if n.track == 0]

# мін. значення → нижня нота C4, макс. → верхня C6
t = cols["temperature_C"]
assert mel[t.argmin()].pitch == 60 and mel[t.argmax()].pitch == 84
# усі ноти належать гамі
assert all((n.pitch - 60) % 12 in S.SCALES["Мажор"] for n in mel)
# velocity у межах 40…120, мін/макс опадів → 40/120
p = cols["precipitation_mm"]
assert mel[p.argmin()].velocity == 40 and mel[p.argmax()].velocity == 120
print("✓ правила висоти та velocity виконуються")

path = os.path.join(tempfile.gettempdir(), "test.mid")
S.write_midi(notes, path, 120, 0)
mid = mido.MidiFile(path)
ons = [m for tr in mid.tracks for m in tr if m.type == "note_on"]
assert mid.type == 1 and len(mid.tracks) == 3 and len(ons) == len(notes)
assert abs(mid.length - len(mel) * 0.5) < 0.05        # 12 нот × 1 доля при 120 BPM = 6 с
print(f"✓ MIDI: тип {mid.type}, доріжок {len(mid.tracks)}, нот {len(ons)}, {mid.length:.2f} с")
print("Усі тести пройдено.")
