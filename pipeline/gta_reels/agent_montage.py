"""Agent 2 : transforme un clip 16:9 en vidéo verticale 9:16 sous-titrée."""
import json
import subprocess
import time
from pathlib import Path

from .commun import DATA, config, db, log
from .redaction import plan_par_defaut, planifier, rediger
from .sous_titres import a_deja_des_sous_titres, traduire
from .controle import juger_clip, trouver_camera, verifier
from .recadrage import expression, suivre
from .voix import dire

L = log("montage")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_whisper = None


def transcrire(video: str, indice: str = "", langue: str = "fr") -> list[dict]:
    """Retourne les mots avec leurs horodatages (Whisper local, gratuit).

    Chaque mot a "sur" = False quand Whisper n'est pas confiant (bruit du jeu, musique, dialogue anglais
    des personnages…) : ces mots ne sont pas affichés en sous-titres, mieux vaut rien qu'un texte faux.
    """
    global _whisper
    from faster_whisper import WhisperModel

    m = config()["montage"]
    if _whisper is None:
        _whisper = WhisperModel(m.get("modele_whisper", "large-v3-turbo"), device="cpu", compute_type="int8")
    segments, _ = _whisper.transcribe(
        video, language=langue, word_timestamps=True, vad_filter=True, beam_size=5,
        condition_on_previous_text=False,  # évite les phrases inventées qui se répètent
        initial_prompt=(f"Stream Twitch en français sur GTA 6. {indice}" if langue == "fr"
                        else f"Twitch stream playing GTA 6. {indice}").strip(),
    )
    mots = []
    for seg in segments:
        seg_ok = seg.avg_logprob > -0.9 and seg.no_speech_prob < 0.6 and seg.compression_ratio < 2.4
        for w in seg.words or []:
            mots.append({"t0": w.start, "t1": w.end, "mot": w.word.strip(),
                         "sur": seg_ok and (w.probability or 0) >= float(m.get("confiance_min", 0.45))})
    return mots


def _ts(s: float) -> str:
    h, r = divmod(max(s, 0), 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def ecrire_ass(mots: list[dict], chemin: Path) -> None:
    """Sous-titres façon TikTok, mot par mot : 3 mots à l'écran, le mot prononcé en jaune et un peu plus gros."""
    entete = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
        "Style: Def,DejaVu Sans,84,&H00FFFFFF,&H00000000,&H96000000,1,1,7,3,2,50,50,520\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Text\n"
    )
    lignes, blocs, bloc = [], [], []
    for w in mots:  # blocs de 3 mots max, coupés aux pauses et aux mots incertains
        if not w.get("sur", True):
            if bloc:
                blocs.append(bloc)
            bloc = []
            continue
        if bloc and (len(bloc) == 3 or w["t0"] - bloc[-1]["t1"] > 0.6):
            blocs.append(bloc)
            bloc = []
        bloc.append(w)
    if bloc:
        blocs.append(bloc)

    def propre(m: str) -> str:
        return m.upper().replace("{", "").replace("}", "").replace("\\", "")

    for k, bloc in enumerate(blocs):
        fin_bloc = bloc[-1]["t1"]
        if k + 1 < len(blocs):  # jamais deux lignes à l'écran en même temps
            fin_bloc = min(fin_bloc, blocs[k + 1][0]["t0"] - 0.01)
        for j, w in enumerate(bloc):  # une ligne par mot : le mot en cours est surligné
            debut = w["t0"]
            fin = bloc[j + 1]["t0"] if j + 1 < len(bloc) else fin_bloc
            fin = min(fin, fin_bloc)
            if fin <= debut:
                continue
            texte = " ".join(
                ("{\\c&H00E5FF&\\fscx112\\fscy112}" + propre(x["mot"]) + "{\\r}") if x is w else propre(x["mot"])
                for x in bloc)
            lignes.append(f"Dialogue: 0,{_ts(debut)},{_ts(fin)},Def,{texte}")
    chemin.write_text(entete + "\n".join(lignes) + "\n", encoding="utf-8")


def phrases(mots: list[dict], max_mots: int = 7) -> list[dict]:
    """Regroupe les mots sûrs en phrases courtes (coupées aux pauses et à la ponctuation)."""
    res, cur = [], []
    for w in mots:
        if not w.get("sur", True):
            if cur:
                res.append(cur)
            cur = []
            continue
        if cur and (len(cur) >= max_mots or w["t0"] - cur[-1]["t1"] > 0.6):
            res.append(cur)
            cur = []
        cur.append(w)
        if w["mot"][-1:] in ".?!":
            res.append(cur)
            cur = []
    if cur:
        res.append(cur)
    return [{"t0": p[0]["t0"], "t1": p[-1]["t1"], "texte": " ".join(w["mot"] for w in p)} for p in res]


def ecrire_ass_phrases(blocs: list[dict], chemin: Path) -> None:
    """Sous-titres traduits : une phrase à la fois (le mot par mot n'a pas de sens après traduction)."""
    entete = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
        "Style: Def,DejaVu Sans,66,&H00FFFFFF,&H00000000,&H96000000,1,1,6,3,2,70,70,520\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Text\n"
    )
    lignes = []
    for k, b in enumerate(blocs):
        fin = max(b["t1"], b["t0"] + 1.0)  # au moins 1 s pour avoir le temps de lire
        if k + 1 < len(blocs):
            fin = min(fin, blocs[k + 1]["t0"] - 0.01)
        if fin <= b["t0"] or not b["texte"].strip():
            continue
        texte = b["texte"].upper().replace("{", "").replace("}", "").replace("\\", "")
        lignes.append(f"Dialogue: 0,{_ts(b['t0'])},{_ts(fin)},Def,{texte}")
    chemin.write_text(entete + "\n".join(lignes) + "\n", encoding="utf-8")


def _a_du_son(video: str) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                        "-of", "csv=p=0", video], capture_output=True, text=True)
    return bool(r.stdout.strip())


def duree(video: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


HAUT_CAM = 640  # hauteur de la caméra du streamer en haut de l'écran (le jeu prend le reste)


def rendre_segment(source: str, sortie: Path, ass: Path | None, credit: str | None,
                   camera: tuple[float, float, float, float] | None = None,
                   debut: float = 0.0, fin: float | None = None) -> None:
    """Un clip -> un segment vertical 1080x1920 qui remplit l'écran.
    - avec caméra : la webcam du streamer en haut, le jeu recadré en grand en dessous ;
    - sans caméra : le jeu recadré en grand au centre, fond flou derrière.
    debut/fin : partie du clip à garder (temps morts retirés)."""
    travail = sortie.parent
    suivi = config()["montage"].get("cadrage_suivi", True)
    couleurs = "eq=contrast=1.06:saturation=1.2"  # image qui claque un peu plus sur un téléphone
    if camera:
        x, y, l, h = camera
        bas = 1920 - HAUT_CAM
        # le jeu est recadré au centre ; si la webcam déborde dans ce cadre, on le décale un peu pour l'éviter
        lj = (1080 / bas) * 9 / 16  # largeur du cadre de jeu, en fraction de l'image (source 16:9)
        x0 = (1 - lj) / 2
        if x + l > x0 and x + l / 2 < 0.5:      # webcam à gauche
            x0 = min(x + l, x0 + 0.15)
        elif x < x0 + lj and x + l / 2 > 0.5:   # webcam à droite
            x0 = max(x - lj, x0 - 0.15)
        decalage = min(max(x0 / (1 - lj), 0.0), 1.0)
        reperes = suivre(source, debut, fin, lj, camera) if suivi else None
        if reperes:  # le cadre qui suit l'action évite quand même la webcam (déjà affichée en haut)
            pmin = (x + l) / (1 - lj) if x + l / 2 < 0.5 and x + l <= 1 - lj else 0.0
            pmax = (x - lj) / (1 - lj) if x + l / 2 > 0.5 and x >= lj else 1.0
            reperes = [(t, min(max(v, pmin), pmax)) for t, v in reperes]
        pos_x = f"(iw-ow)*({expression(reperes)})" if reperes else f"(iw-ow)*{decalage:.4f}"
        filtres = [
            "[0:v]split[s1][s2]",
            f"[s1]crop=iw*{l:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f},"
            f"scale=1080:{HAUT_CAM}:force_original_aspect_ratio=increase,crop=1080:{HAUT_CAM},setsar=1[cam]",
            f"[s2]crop=min(iw\\,ih*{1080 / bas:.4f}):ih:{pos_x}:0,scale=1080:{bas},setsar=1[jeu]",
            f"[cam][jeu]vstack,{couleurs},setsar=1[v0]",
        ]
    else:
        reperes = suivre(source, debut, fin, 0.8 * 9 / 16) if suivi else None
        pos_x = f"(iw-ow)*({expression(reperes)})" if reperes else "(iw-ow)/2"
        filtres = [
            "[0:v]split[s1][s2]",
            "[s1]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5,"
            "eq=brightness=-0.2[fond]",
            f"[s2]crop=min(iw\\,ih*0.8):ih:{pos_x}:0,scale=1080:1350,setsar=1[jeu]",
            f"[fond][jeu]overlay=0:285,{couleurs},setsar=1[v0]",
        ]
    courant = "v0"
    if ass:
        filtres.append(f"[{courant}]subtitles={ass.name}[v1]")
        courant = "v1"
    if credit:
        (travail / "credit.txt").write_text(credit, encoding="utf-8")
        filtres.append(
            f"[{courant}]drawtext=fontfile={FONT}:textfile=credit.txt:fontsize=40:fontcolor=white@0.9:"
            f"borderw=3:bordercolor=black:x=(w-text_w)/2:y=h-240[v3]"
        )
        courant = "v3"
    coupe = ["-ss", f"{debut:.3f}"] + (["-to", f"{fin:.3f}"] if fin else [])
    entrees = [*coupe, "-i", source]
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
    return textwrap.wrap(texte, largeur, break_long_words=False, break_on_hyphens=False)[:4]  # jamais un mot coupé


def carte(segment: str, sortie: Path, gros: str, petit: str | None, voix: Path | None, duree_voix: float | None,
          duree_min: float, image_a: float = 0.5) -> float:
    """Écran d'annonce : image floutée du clip, gros texte, voix-off. Renvoie sa durée."""
    travail = sortie.parent
    image = travail / f"{sortie.stem}.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{image_a:.2f}", "-i", segment, "-frames:v", "1", str(image)],
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


def habiller(segment: str, sortie: Path, gros: str | None, petit: str | None, voix: Path | None,
            duree_voix: float | None, duree_min: float = 2.5) -> None:
    """Pose un titre sur le début du clip (pas d'écran figé) et y mixe la voix-off, le son du jeu baissé.
    La vidéo démarre donc directement sur l'action."""
    travail = sortie.parent
    d_clip = duree(segment)
    d = min(d_clip, max(duree_min, (duree_voix or 0) + 0.4))
    filtres = []
    y = HAUT_CAM + 30
    if gros:
        court = len(gros) <= 4  # « #3 », « 1/2 »…
        taille = 190 if court else 86
        for i, ligne in enumerate(_lignes(gros, 7 if court else 16)[:3]):
            f = travail / f"{sortie.stem}_l{i}.txt"
            f.write_text(ligne, encoding="utf-8")
            filtres.append(f"drawtext=fontfile={FONT}:textfile={f.name}:fontsize={taille}:fontcolor=white:"
                           f"borderw=8:bordercolor=black:box=1:boxcolor=black@0.45:boxborderw=18:"
                           f"x=(w-text_w)/2:y={y}:enable='lt(t,{d:.2f})'")
            y += int(taille * 1.25)
    if petit:
        f = travail / f"{sortie.stem}_p.txt"
        f.write_text(petit, encoding="utf-8")
        filtres.append(f"drawtext=fontfile={FONT}:textfile={f.name}:fontsize=60:fontcolor=0xFFD65A:"
                       f"borderw=5:bordercolor=black:x=(w-text_w)/2:y={y + 10}:enable='lt(t,{d:.2f})'")
    video = "[0:v]" + (",".join(filtres) if filtres else "null") + "[v]"
    entrees = ["-i", segment]
    if voix and duree_voix:
        entrees += ["-i", voix.name]
        audio = (f"[0:a]volume=0.3:enable='lt(t,{duree_voix + 0.3:.2f})'[jeu];"
                 "[1:a]aresample=48000,aformat=channel_layouts=stereo,adelay=150|150[vo];"
                 "[jeu][vo]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        audio = "[0:a]anull[a]"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *entrees, "-filter_complex", video + ";" + audio,
           "-map", "[v]", "-map", "[a]", *ENCODAGE, sortie.name]
    subprocess.run(cmd, check=True, cwd=travail, timeout=600)


def assembler(morceaux: list[str], sortie: Path, duree_max: float) -> None:
    """Colle cartes et clips bout à bout, en entier (ne coupe jamais la fin)."""
    entrees = [x for m in morceaux for x in ("-i", m)]
    n = len(morceaux)
    chaine = ("".join(f"[{i}:v]scale=1080:1920,setsar=1,fps=30[v{i}];" for i in range(n))
              + "".join(f"[v{i}][{i}:a]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *entrees, "-filter_complex", chaine,
           "-map", "[v]", "-map", "[a]", "-t", "600", *ENCODAGE, "-crf", "23", "-maxrate", "6M", "-bufsize", "12M",  # fichier plus léger = traité plus vite par TikTok ; jamais coupé (10 min max)
           "-movflags", "+faststart", sortie.name]
    subprocess.run(cmd, check=True, cwd=sortie.parent, timeout=1800)


def _horodater(mots: list[dict]) -> str:
    """Transcription avec repères de temps (« [12s] … ») pour que Claude sache où se passe quoi."""
    morceaux, debut = [], None
    for w in mots:
        if debut is None or w["t0"] - debut >= 4:
            debut = w["t0"]
            morceaux.append(f"[{int(debut)}s]")
        morceaux.append(w["mot"])
    return " ".join(morceaux)


def couper(segment: str, sortie: Path, debut: float, fin: float | None) -> None:
    """Extrait un morceau d'un segment (réencodé pour un assemblage propre)."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{debut:.3f}", "-i", segment]
    if fin is not None:
        cmd += ["-t", f"{fin - debut:.3f}"]
    subprocess.run(cmd + [*ENCODAGE, str(sortie)], check=True, timeout=600)


def preparer_segment(clip) -> None:
    m = config()["montage"]
    dossier = DATA / "segments" / clip["id"]
    dossier.mkdir(parents=True, exist_ok=True)
    # Certains streamers ont déjà des sous-titres incrustés dans leur stream : on n'en rajoute pas
    avec_st = m["sous_titres"] and not a_deja_des_sous_titres(clip["fichier_brut"], clip["streamer_login"],
                                                               duree(clip["fichier_brut"]))
    # transcription toujours faite : Claude en a besoin. Le pseudo et le titre aident Whisper à bien écrire.
    langue = (clip["langue"] if "langue" in clip.keys() else None) or "fr"
    mots = transcrire(clip["fichier_brut"], f"Streamer : {clip['streamer']}. {clip['titre'] or ''}", langue)
    # Claude juge le clip : règles TikTok (nudité, sexe, gore…) et intérêt sur 10. Seuls les bons clips passent.
    d_brut = duree(clip["fichier_brut"])
    ok, note, raison, debut, fin = juger_clip(clip["fichier_brut"], d_brut, clip["streamer"],
                                              clip["titre"] or "", _horodater(mots), langue)
    if ok is None:  # Claude indisponible : on ne monte rien sans jugement, le clip attend le prochain passage
        essais = int((clip["erreur"] or "").split("essai ")[-1]) + 1 if "jugement : essai " in (clip["erreur"] or "") else 1
        conn = db()  # au bout de 3 échecs, on abandonne ce clip pour ne pas payer indéfiniment
        conn.execute("UPDATE clips SET statut=?, erreur=? WHERE id=?",
                     ("erreur" if essais >= 3 else clip["statut"], f"jugement : essai {essais}", clip["id"]))
        conn.commit()
        return
    note_min = float(m.get("note_min", 7))
    if not ok or note < note_min:
        conn = db()
        motif = ("règles TikTok : " if not ok else f"note {note:.0f}/10 : ") + raison
        conn.execute("UPDATE clips SET statut='refuse', erreur=? WHERE id=?", (motif[:500], clip["id"]))
        conn.commit()
        L.info("Clip écarté — %s (%s) — %s", clip["titre"], clip["streamer"], motif)
        return
    L.info("Clip retenu, note %.0f/10 — %s (%s)", note, clip["titre"], clip["streamer"])
    # temps morts : on garde seulement le moment utile repéré par Claude (s'il reste assez long)
    debut, fin = max(0.0, debut), min(d_brut, fin)
    if fin - debut < max(6.0, 0.35 * d_brut) or (debut < 0.5 and fin > d_brut - 0.5):
        debut, fin = 0.0, d_brut
    else:
        L.info("Temps morts retirés : on garde %.0f s → %.0f s sur %.0f s", debut, fin, d_brut)
    mots = [dict(w, t0=w["t0"] - debut, t1=w["t1"] - debut) for w in mots if w["t0"] >= debut and w["t1"] <= fin]
    camera = trouver_camera(clip["fichier_brut"], d_brut) if m.get("camera_en_haut", True) else None
    ass = None
    if avec_st and sum(w["sur"] for w in mots) >= 3:
        ass = dossier / "subs.ass"
        if langue == "fr":
            ecrire_ass(mots, ass)
        else:  # streamer étranger : sous-titres traduits en français, phrase par phrase
            blocs = phrases(mots)
            trad = traduire([b["texte"] for b in blocs], clip["streamer"])
            if trad is None:
                ass = None
            else:
                ecrire_ass_phrases([dict(b, texte=t) for b, t in zip(blocs, trad)], ass)
    sortie = dossier / "segment.mp4"
    rendre_segment(clip["fichier_brut"], sortie, ass, f"@{clip['streamer']} sur Twitch" if m["credit"] else None,
                   camera, debut, fin if fin < d_brut - 0.05 else None)
    conn = db()
    conn.execute(
        "UPDATE clips SET statut='segment', fichier_segment=?, duree_segment=?, transcription=?, score=? WHERE id=?",
        (str(sortie), duree(str(sortie)), _horodater(mots), note, clip["id"]),  # score = note de Claude sur 10
    )
    conn.commit()
    L.info("Segment prêt : %s (%s)", clip["titre"], clip["streamer"])


_dernier_plan = 0.0


def _infos(c) -> dict:
    return {"id": c["id"], "streamer": c["streamer"], "login": c["streamer_login"], "titre": c["titre"] or "",
            "transcription": c["transcription"] or "", "duree": c["duree_segment"] or 0, "score": c["score"] or 0,
            "langue": (c["langue"] if "langue" in c.keys() else None) or "fr"}


CARTES_PAR_CLIP = {"top", "recap", "serie", "duel", "astuce"}
NUMEROTE = {"top", "recap"}


def fabriquer_partie(ordre: list, fmt: str, partie: int, parties: int, serie: str | None, dmax: float) -> None:
    """Monte une vidéo (ou une partie de série) à partir de segments déjà prêts, dans l'ordre donné."""
    m = config()["montage"]
    conn = db()
    n = len(ordre)
    ids = [c["id"] for c in ordre]
    redac = rediger([_infos(c) for c in ordre], fmt, partie, parties)
    dossier = DATA / "final" / ("-".join(ids)[:70] + (f"-p{partie}" if serie else ""))
    dossier.mkdir(parents=True, exist_ok=True)
    sortie = dossier / "video.mp4"
    voix = m.get("voix_off", True)

    def voix_off(texte: str, nom: str):
        f = dossier / f"{nom}.wav"
        return f, (dire(texte, f) if voix and texte else None)

    morceaux, en_pause, habilles = [], False, {}
    gros_intro = redac["hook"]
    if fmt == "serie" and f"{partie}/{parties}" not in gros_intro:
        gros_intro = f"PARTIE {partie}/{parties} {gros_intro}"
    for i, c in enumerate(ordre):
        # textes posés sur le début du clip (plus d'écran figé : la vidéo démarre sur l'action)
        if fmt in NUMEROTE:
            gros, petit = f"#{n - i}", c["streamer"]
        elif fmt == "duel":
            gros, petit = f"CHOIX {i + 1}", c["streamer"]
        elif fmt in CARTES_PAR_CLIP and n > 1:
            gros, petit = None, c["streamer"]
        else:
            gros, petit = None, None
        texte_voix = redac["annonces"][i] if fmt in CARTES_PAR_CLIP and i < len(redac["annonces"]) else ""
        if i == 0:  # accroche en gros sur le premier clip, l'intro lue par-dessus
            petit = " · ".join(x for x in (gros if gros and len(gros) <= 4 else None, petit) if x) or None
            gros = gros_intro
            texte_voix = " ".join(x for x in (redac["intro"], texte_voix) if x)
        va, da = voix_off(texte_voix, f"a{i}")
        seg = c["fichier_segment"]
        pause = redac.get("pause_s") or 0
        coupe_devine = fmt == "devine" and 2 <= pause <= (c["duree_segment"] or 0) - 2
        if coupe_devine:
            # on coupe juste avant le moment clé et on fait deviner la suite
            couper(seg, dossier / "avant_brut.mp4", 0, pause)
            seg = str(dossier / "avant_brut.mp4")
        if gros or petit or da:
            habiller(seg, dossier / f"h{i}.mp4", gros, petit, va, da, 3.0 if i == 0 else 2.2)
            habilles[str(dossier / f"h{i}.mp4")] = (c, gros, petit)
            seg = str(dossier / f"h{i}.mp4")
        morceaux.append(seg)
        if coupe_devine:
            vq, dq = voix_off(redac["question"] or "À ton avis, qu'est-ce qui va se passer ?", "devine")
            carte(seg, dossier / "c_devine.mp4", "QUE VA-T-IL SE PASSER ?", "Réponds en commentaire",
                  vq, dq, 3.0, image_a=max(duree(seg) - 0.5, 0.1))
            couper(c["fichier_segment"], dossier / "apres.mp4", pause, None)
            morceaux += [str(dossier / "c_devine.mp4"), str(dossier / "apres.mp4")]
            en_pause = True
    if redac.get("suite"):
        vs, ds = voix_off(redac["suite"], "suite")
        carte(ordre[-1]["fichier_segment"], dossier / "c_suite.mp4", f"LA SUITE DANS LA PARTIE {partie + 1}", "Abonne-toi",
              vs, ds, 2.5, image_a=max((ordre[-1]["duree_segment"] or 1) - 0.5, 0.1))
        morceaux.append(str(dossier / "c_suite.mp4"))
    elif redac.get("question") and not en_pause:
        vq, dq = voix_off(redac["question"], "question")
        carte(ordre[-1]["fichier_segment"], dossier / "c_question.mp4", redac["question"], "Dis-le en commentaire",
              vq, dq, 3.0, image_a=max((ordre[-1]["duree_segment"] or 1) - 0.5, 0.1))
        morceaux.append(str(dossier / "c_question.mp4"))
    assembler(morceaux, sortie, dmax)

    # contrôle qualité avant d'entrer dans le stock
    noms = {c["fichier_segment"]: c for c in ordre}
    etiquettes = []
    for mc in morceaux:
        nom = Path(mc).stem
        if mc in noms:
            etiquettes.append(f"Clip de {noms[mc]['streamer']} : {noms[mc]['titre']}")
        elif mc in habilles:
            c, gros, petit = habilles[mc]
            k = ordre.index(c)
            txt = " / ".join(x for x in (gros, petit) if x)
            etiquettes.append(f"Clip {k + 1} sur {n}, de {c['streamer']} : {c['titre']}"
                              + (f" (texte posé sur le début : « {txt} »)" if txt else ""))
        else:
            etiquettes.append({"c_devine": "Carte « que va-t-il se passer ? »",
                               "apres": "Suite du clip (réponse)", "c_suite": "Carte « la suite dans la partie suivante »",
                               "c_question": "Carte question finale"}.get(nom, nom))
    ok, problemes = verifier(sortie, morceaux, etiquettes, fmt, redac)
    statut = "prete" if ok else "rejetee"
    if not ok:
        L.warning("Vidéo refusée par le contrôle qualité : %s", " | ".join(problemes))

    score = sum(c["score"] or 0 for c in ordre) / n
    conn.execute(
        "INSERT INTO videos (fichier, clips, score, hook, legende, format, serie, partie, parties, duree, statut, erreur) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (str(sortie), ",".join(ids), score, redac["hook"], redac["legende_complete"], fmt, serie, partie, parties,
         duree(str(sortie)), statut, " | ".join(problemes)[:500] or None),
    )
    if not ok and serie:  # une série avec une partie refusée ne doit pas sortir à moitié
        conn.execute("UPDATE videos SET statut='rejetee' WHERE serie=?", (serie,))
    conn.executemany("UPDATE clips SET statut='compile' WHERE id=?", [(i,) for i in ids])
    conn.commit()
    L.info("Vidéo prête : %s%s, %d clip(s), %.0f s", fmt, f" partie {partie}/{parties}" if serie else "", n,
           duree(str(sortie)))


def compiler(forcer: bool = False) -> int:
    """Demande à Claude un plan éditorial quand assez de clips attendent, puis fabrique les vidéos."""
    global _dernier_plan
    m = config()["montage"]
    dmin, dmax = float(m.get("duree_min_s", 15)), float(m.get("duree_max_s", 180))
    conn = db()
    dispo = conn.execute("SELECT * FROM clips WHERE statut='segment' ORDER BY score DESC").fetchall()
    if not dispo:
        return 0
    stock = conn.execute("SELECT COUNT(*) FROM videos WHERE statut='prete'").fetchone()[0]
    attente_min = (time.time() - _dernier_plan) / 60
    assez = len(dispo) >= int(m.get("plan_min_clips", 3))
    if not forcer and not ((assez and attente_min >= 20) or (stock == 0 and attente_min >= 20)):
        return 0
    _dernier_plan = time.time()

    infos = [_infos(c) for c in dispo]
    try:
        plan = planifier(infos, dmin, dmax)
    except Exception as e:
        L.warning("Plan IA indisponible (%s), plan simple utilisé", e)
        plan = plan_par_defaut(infos, dmin, dmax)
    par_id = {c["id"]: c for c in dispo}
    faites = 0
    for v in plan:
        parties = v["parties"]
        serie = f"s{int(time.time())}-{faites}" if v["format"] == "serie" else None
        for k, ids in enumerate(parties, start=1):
            ordre = [par_id[i] for i in ids]
            if v["format"] in ("top", "recap"):  # compte à rebours : le meilleur clip en dernier
                ordre.sort(key=lambda c: c["score"] or 0)
            try:
                fabriquer_partie(ordre, v["format"], k, len(parties), serie, dmax)
                faites += 1
            except Exception as e:
                L.error("Échec du montage %s : %s", ids, e)
                if serie:  # on retire les parties déjà faites pour ne jamais publier une série à moitié
                    conn.execute("UPDATE videos SET statut='erreur', erreur='série incomplète' WHERE serie=?", (serie,))
                for i in ids:
                    conn.execute("UPDATE clips SET statut='erreur', erreur=? WHERE id=?", (str(e)[:500], i))
                conn.commit()
                break  # une série incomplète ne doit pas être publiée à moitié
    return faites


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
