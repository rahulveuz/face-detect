import itertools
from pathlib import Path
import numpy as np
from django.core.management.base import BaseCommand
from face_recognition.engine import embed, FaceError


class Command(BaseCommand):
    help = "Measure genuine/impostor score distributions and 1:N accuracy"

    def add_arguments(self, parser):
        parser.add_argument("folder")

    def handle(self, *args, folder, **kw):
        out = self.stdout.write
        data = {}                                   # person -> [(filename, vec)]
        for p in sorted(Path(folder).iterdir()):
            if not p.is_dir():
                continue
            for img in sorted(p.glob("*")):
                try:
                    data.setdefault(p.name, []).append((img.name, embed(img.read_bytes())))
                except FaceError as e:
                    out(f"SKIPPED {p.name}/{img.name}: {e}")

        known = {n: v for n, v in data.items() if not n.startswith("unknown_")}

        # ---- genuine vs impostor ----
        genuine = [float(a @ b) for v in data.values() for (_, a), (_, b) in itertools.combinations(v, 2)]
        impostor = [float(a @ b) for n1, n2 in itertools.combinations(data, 2)
                    for _, a in data[n1] for _, b in data[n2]]
        g, i = np.array(genuine), np.array(impostor)
        out(f"\nGenuine pairs : {len(g)}  min={g.min():.3f} mean={g.mean():.3f} max={g.max():.3f}")
        out(f"Impostor pairs: {len(i)}  min={i.min():.3f} mean={i.mean():.3f} max={i.max():.3f}")
        out("\nthreshold   FAR (strangers accepted)   FRR (real people rejected)")
        for t in np.arange(0.10, 0.80, 0.05):
            out(f"  {t:.2f}        {np.mean(i >= t)*100:6.2f}%                  {np.mean(g < t)*100:6.2f}%")

        # ---- 1:N test: enroll first photo of each known person, probe with the rest ----
        gallery_ids = list(known)
        gallery = np.stack([known[n][0][1] for n in gallery_ids])
        out("\n1:N test (enrolled = first photo of each known person)")
        probes = [(n, n, v) for n in known for _, v in known[n][1:]] + \
                 [(n, None, v) for n in data if n.startswith("unknown_") for _, v in data[n]]
        wrong, rows = 0, []
        for who, truth, v in probes:
            s = gallery @ v
            order = np.argsort(-s)
            top, second = s[order[0]], s[order[1]] if len(s) > 1 else -1
            rows.append((who, truth, gallery_ids[order[0]], top, top - second))
            if truth is not None and gallery_ids[order[0]] != truth:
                wrong += 1
        known_rows = [r for r in rows if r[1] is not None]
        out(f"Rank-1 accuracy on enrolled people (ignoring threshold): "
            f"{100*(1-wrong/max(1,len(known_rows))):.1f}%  ({wrong} wrong of {len(known_rows)})")
        for r in rows:
            who, truth, best, top, margin = r
            tag = "" if truth is None or best == truth else "   <-- WRONG PERSON"
            out(f"  probe={who:15s} best={best:10s} score={top:.3f} margin={margin:.3f}{tag}")