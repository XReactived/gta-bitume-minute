"""Agent 2 : transforme un clip 16:9 en vidéo verticale 9:16 sous-titrée."""
import json
import subprocess
from pathlib import Path

from .commun import DATA, config, db, log
from .redaction import rediger
from .voix import dire

L = log("montage")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_whisper = None


def transcrire(video: str) -> list[dict]:
    """Retourne les mots avec leurs horodatages (Whisper local, gratuit)."""
    global _whisper
    from faster_whisper import WhisperModel

    if _whisper is None:
        _whisper = WhisperModel(config()["montage"]["modele_whisper"], device="cpu", compute_type="int8")
    segments, _ = _whisper.transcribe(video, language="fr", word_timestamps=True, vad_filter=True)
    return [{"t0": w.start, "t1": w.end, "mot": w.word.strip()} for s in segments for w in (s.words or [])]


def _ts(s: float) -> str:
    h, r = divmod(max(s, 0), 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def ecrire_ass(mots: list[dict], chemin: Path) -> None:
    """Sous-titres façon TikTok : 3 mots à la fois, gros, jaunes, contour noir."""
    entete = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
        "Style: Def,DejaVu Sans,78,&H0000F0FF,&H00000000,&H64000000,1,1,6,2,2,60,60,560\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Text\n"
    )
    lignes = []
    for i in range(0, len(mots), 3):
        bloc = mots[i:i + 3]
        texte = " ".join(m["mot"] for m in bloc).upper().replace("{", "").replace("}", "")
        lignes.append(f"Dialogue: 0,{_ts(bloc[0]['t0'])},{_ts(bloc[-1]['t1'])},Def,{texte}")
    chemin.write_text(entete + "\n".join(lignes) + "\n", encoding="utf-8")


def _a_du_son(video: str) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                        "-of", "csv=p=0", video], capture_output=True, text=True)
    return bool(r.stdout.strip())


def duree(video: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def rendre_segment(source: str, sortie: Path, ass: Path | None, credit: str | None) -> None:
    """Un clip -> un segment vertical 1080x1920 (fond flouté, sous-titres, crédit)."""
    travail = sortie.parent
    filtres = [
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5,eq=brightness=-0.15[fond]",
        "[0:v]scale=1080:-2[avant]",
        "[fond][avant]overlay=(W-w)/2:(H-h)/2[v0]",
    ]
    courant = "v0"
    if ass:
        filtres.append(f"[{courant}]subtitles={ass.name}[v1]")
        courant = "v1"
    if credit:
        (travail / "credit.txt").write_text(credit, encoding="utf-8")
        filtres.append(
            f"[{courant}]drawtext=fontfile={FONT}:textfile=credit.txt:fontsize=42:fontcolor=white@0.9:"
            f"borderw=3:bordercolor=black:x=(w-text_w)/2:y=h-260[v3]"
        )
        courant = "v3"
    entrees = ["-i", source]
    audio = "0:a"
    if not _a_du_son(source):  # garantit une piste son pour pouvoir assembler
        entrees += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        audio = "1:a"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", *entrees,
        "-filter_complex", ";".join(filtres), "-map", f"[{courant}]", "-map", audio, "-shortest",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", "30", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", sortie.name,
    ]
    subprocess.run(cmd, check=True, cwd=travail, timeout=900)


ENCODAGE = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", "30", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]


def _lignes(texte: str, largeur: int) -> list[str]:
    import textwrap
    return textwrap.wrap(texte, largeur)[:4]


def carte(segment: str, sortie: Path, gros: str, petit: str | None, voix: Path | None, duree_voix: float | None,
          duree_min: float) -> float:
    """Écran d'annonce : image floutée du clip, gros texte, voix-off. Renvoie sa durée."""
    travail = sortie.parent
    image = travail / f"{sortie.stem}.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.5", "-i", segment, "-frames:v", "1", str(image)],
                   check=True, timeout=120)
    d = max(duree_min, (duree_voix or 0) + 0.5)
    filtres = ["[0:v]boxblur=20:4,eq=brightness=-0.3"]
    lignes = _lignes(gros, 12)
    taille = 240 if len(gros) <= 4 else 92
    y0 = 760 - len(lignes) * taille * 0.6
    for i, ligne in enumerate(lignes):
        f = travail / f"{sortie.stem}_l{i}.txt"
        f.write_text(ligne, encoding="utf-8")
        filtres.append(f"drawtext=fontfile={FONT}:textfile={f.name}:fontsize={taille}:fontcolor=white:"
                       f"borderw=8:bordercolor=black:x=(w-text_w)/2:y={y0 + i * taille * 1.2:.0f}")
    if petit:
        f = travail / f"{sortie.stem}_p.txt"
        f.write_text(petit, encoding="utf-8")
        filtres.append(f"drawtext=fontfile={FONT}:textfile={f.name}:fontsize=56:fontcolor=0xFFD65A:"
                       f"borderw=5:bordercolor=black:x=(w-text_w)/2:y={y0 + len(lignes) * taille * 1.2 + 40:.0f}")
    entrees = ["-loop", "1", "-framerate", "30", "-t", f"{d:.3f}", "-i", image.name]
    if voix and duree_voix:
        entrees += ["-i", voix.name]
        audio = f"[1:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={d:.3f}[a]"
    else:
        entrees += ["-f", "lavfi", "-t", f"{d:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        audio = "[1:a]anull[a]"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *entrees,
           "-filter_complex", ",".join(filtres) + "[v];" + audio,
           "-map", "[v]", "-map", "[a]", "-t", f"{d:.3f}", *ENCODAGE, sortie.name]
    subprocess.run(cmd, check=True, cwd=travail, timeout=300)
    return d


def assembler(morceaux: list[str], sortie: Path, cible: float) -> None:
    """Colle cartes et clips bout à bout et coupe à la durée cible exacte."""
    entrees = [x for m in morceaux for x in ("-i", m)]
    n = len(morceaux)
    chaine = "".join(f"[{i}:v][{i}:a]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *entrees, "-filter_complex", chaine,
           "-map", "[v]", "-map", "[a]", "-t", f"{cible:.3f}", *ENCODAGE, "-movflags", "+faststart", sortie.name]
    subprocess.run(cmd, check=True, cwd=sortie.parent, timeout=1800)


def preparer_segment(clip) -> None:
    m = config()["montage"]
    dossier = DATA / "segments" / clip["id"]
    dossier.mkdir(parents=True, exist_ok=True)
    mots = transcrire(clip["fichier_brut"]) if m["sous_titres"] else []
    ass = None
    if mots:
        ass = dossier / "subs.ass"
        ecrire_ass(mots, ass)
    sortie = dossier / "segment.mp4"
    rendre_segment(clip["fichier_brut"], sortie, ass, f"@{clip['streamer']} sur Twitch" if m["credit"] else None)
    conn = db()
    conn.execute(
        "UPDATE clips SET statut='segment', fichier_segment=?, duree_segment=?, transcription=? WHERE id=?",
        (str(sortie), duree(str(sortie)), " ".join(x["mot"] for x in mots), clip["id"]),
    )
    conn.commit()
    L.info("Segment prêt : %s (%s)", clip["titre"], clip["streamer"])


def _choisir(dispo, cible: float, intro: float, par_carte: float, max_clips: int):
    choisis, total = [], intro
    for c in dispo:
        if len(choisis) >= max_clips:
            break
        choisis.append(c)
        total += par_carte + c["duree_segment"]
        if total >= cible:
            return choisis
    return None


def compiler() -> int:
    """Fabrique des tops de durée cible : intro, puis #N … #1 annoncés à la voix avant chaque clip."""
    m = config()["montage"]
    cible = float(m["duree_cible_s"])
    conn = db()
    faites = 0
    while True:
        dispo = conn.execute("SELECT * FROM clips WHERE statut='segment' ORDER BY score DESC").fetchall()
        choisis = _choisir(dispo, cible, 3.0, 2.5, int(m.get("max_clips", 4)))
        if not choisis:
            L.info("%d clip(s) en attente, pas encore assez pour un top de %.0f s", len(dispo), cible)
            return faites
        ordre = list(reversed(choisis))  # compte à rebours : le meilleur clip passe en dernier
        n = len(ordre)
        ids = [c["id"] for c in ordre]
        infos = [{"streamer": c["streamer"], "login": c["streamer_login"], "titre": c["titre"] or "",
                  "transcription": c["transcription"] or ""} for c in ordre]
        redac = rediger(infos)

        dossier = DATA / "final" / "-".join(ids)[:80]
        dossier.mkdir(parents=True, exist_ok=True)
        sortie = dossier / "video.mp4"
        try:
            morceaux = []
            avec_voix = m.get("voix_off", True)
            vi = dossier / "intro.wav"
            di = dire(redac["intro"], vi) if avec_voix else None
            morceaux.append(str(dossier / "c_intro.mp4"))
            carte(ordre[0]["fichier_segment"], dossier / "c_intro.mp4", redac["hook"], None, vi, di, 2.5)
            for i, c in enumerate(ordre):
                va = dossier / f"a{i}.wav"
                da = dire(redac["annonces"][i], va) if avec_voix else None
                nom = dossier / f"c{i}.mp4"
                carte(c["fichier_segment"], nom, f"#{n - i}", c["streamer"], va, da, 2.0)
                morceaux += [str(nom), c["fichier_segment"]]
            assembler(morceaux, sortie, cible)
        except Exception as e:
            L.error("Échec de l'assemblage %s : %s", ids, e)
            for i in ids:  # on écarte ces clips pour ne pas boucler sur la même erreur
                conn.execute("UPDATE clips SET statut='erreur', erreur=? WHERE id=?", (str(e)[:500], i))
            conn.commit()
            continue
        score = sum(c["score"] or 0 for c in choisis) / n
        conn.execute(
            "INSERT INTO videos (fichier, clips, score, hook, legende) VALUES (?,?,?,?,?)",
            (str(sortie), ",".join(ids), score, redac["hook"], redac["legende_complete"]),
        )
        conn.executemany("UPDATE clips SET statut='compile' WHERE id=?", [(i,) for i in ids])
        conn.commit()
        faites += 1
        L.info("Top de %.0f s prêt (%d clips) : %s", cible, n, sortie)


def lancer() -> int:
    conn = db()
    faites = compiler()  # au cas où des segments attendent déjà
    for clip in conn.execute("SELECT * FROM clips WHERE statut='telecharge' ORDER BY score DESC").fetchall():
        try:
            preparer_segment(clip)
            faites += compiler()  # une vidéo dès qu'il y a assez de segments, sans attendre la fin du lot
        except Exception as e:
            conn.execute("UPDATE clips SET statut='erreur', erreur=? WHERE id=?", (str(e)[:500], clip["id"]))
            conn.commit()
            L.error("Échec du montage %s : %s", clip["id"], e)
    return faites


if __name__ == "__main__":
    lancer()
