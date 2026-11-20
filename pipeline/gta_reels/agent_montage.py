"""Agent 2 : transforme un clip 16:9 en vidéo verticale 9:16 sous-titrée."""
import json
import subprocess
import time
from pathlib import Path

from .commun import DATA, config, db, log
from .redaction import plan_par_defaut, planifier, rediger
from .sous_titres import a_deja_des_sous_titres
from .controle import juger_clip, verifier
from .voix import dire

L = log("montage")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_whisper = None


def transcrire(video: str, indice: str = "") -> list[dict]:
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
        video, language="fr", word_timestamps=True, vad_filter=True, beam_size=5,
        condition_on_previous_text=False,  # évite les phrases inventées qui se répètent
        initial_prompt=f"Stream Twitch en français sur GTA 6. {indice}".strip(),
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
    """Sous-titres façon TikTok : 3 mots à la fois, gros, jaunes, contour noir."""
    entete = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
        "Style: Def,DejaVu Sans,78,&H0000F0FF,&H00000000,&H64000000,1,1,6,2,2,60,60,560\n\n"
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
    for k, bloc in enumerate(blocs):
        texte = " ".join(m["mot"] for m in bloc).upper().replace("{", "").replace("}", "")
        debut, fin = bloc[0]["t0"], bloc[-1]["t1"]
        if k + 1 < len(blocs):  # jamais deux lignes à l'écran en même temps
            fin = min(fin, blocs[k + 1][0]["t0"] - 0.01)
        if fin <= debut:
            continue
        lignes.append(f"Dialogue: 0,{_ts(debut)},{_ts(fin)},Def,{texte}")
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


def assembler(morceaux: list[str], sortie: Path, duree_max: float) -> None:
    """Colle cartes et clips bout à bout, en entier (ne coupe jamais la fin)."""
    entrees = [x for m in morceaux for x in ("-i", m)]
    n = len(morceaux)
    chaine = "".join(f"[{i}:v][{i}:a]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
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
    mots = transcrire(clip["fichier_brut"], f"Streamer : {clip['streamer']}. {clip['titre'] or ''}")
    # Claude juge le clip : règles TikTok (nudité, sexe, gore…) et intérêt sur 10. Seuls les bons clips passent.
    ok, note, raison = juger_clip(clip["fichier_brut"], duree(clip["fichier_brut"]), clip["streamer"],
                                  clip["titre"] or "", _horodater(mots))
    if ok is None:  # Claude indisponible : on ne monte rien sans jugement, le clip attend le prochain passage
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
    ass = None
    if avec_st and sum(w["sur"] for w in mots) >= 3:
        ass = dossier / "subs.ass"
        ecrire_ass(mots, ass)
    sortie = dossier / "segment.mp4"
    rendre_segment(clip["fichier_brut"], sortie, ass, f"@{clip['streamer']} sur Twitch" if m["credit"] else None)
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
            "transcription": c["transcription"] or "", "duree": c["duree_segment"] or 0, "score": c["score"] or 0}


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

    morceaux, en_pause = [], False
    vi, di = voix_off(redac["intro"], "intro")
    gros_intro = redac["hook"]
    if fmt == "serie" and f"{partie}/{parties}" not in gros_intro:
        gros_intro = f"PARTIE {partie}/{parties} {gros_intro}"
    carte(ordre[0]["fichier_segment"], dossier / "c_intro.mp4", gros_intro, None, vi, di, 2.5)
    morceaux.append(str(dossier / "c_intro.mp4"))
    for i, c in enumerate(ordre):
        if fmt in CARTES_PAR_CLIP:
            va, da = voix_off(redac["annonces"][i], f"a{i}")
            if fmt in NUMEROTE:
                gros, petit = f"#{n - i}", c["streamer"]
            elif fmt == "duel":
                gros, petit = f"CHOIX {i + 1}", c["streamer"]
            else:
                gros, petit = c["streamer"], None
            carte(c["fichier_segment"], dossier / f"c{i}.mp4", gros, petit, va, da, 2.0)
            morceaux.append(str(dossier / f"c{i}.mp4"))
        pause = redac.get("pause_s") or 0
        if fmt == "devine" and 2 <= pause <= (c["duree_segment"] or 0) - 2:
            # on coupe juste avant le moment clé et on fait deviner la suite
            couper(c["fichier_segment"], dossier / "avant.mp4", 0, pause)
            vq, dq = voix_off(redac["question"] or "À ton avis, qu'est-ce qui va se passer ?", "devine")
            carte(str(dossier / "avant.mp4"), dossier / "c_devine.mp4", "QUE VA-T-IL SE PASSER ?", "Réponds en commentaire",
                  vq, dq, 3.0, image_a=max(pause - 1.0, 0.1))
            couper(c["fichier_segment"], dossier / "apres.mp4", pause, None)
            morceaux += [str(dossier / "avant.mp4"), str(dossier / "c_devine.mp4"), str(dossier / "apres.mp4")]
            en_pause = True
        else:
            morceaux.append(c["fichier_segment"])
    if redac.get("suite"):
        vs, ds = voix_off(redac["suite"], "suite")
        carte(ordre[-1]["fichier_segment"], dossier / "c_suite.mp4", f"LA SUITE DANS LA PARTIE {partie + 1}", "Abonne-toi",
              vs, ds, 2.5)
        morceaux.append(str(dossier / "c_suite.mp4"))
    elif redac.get("question") and not en_pause:
        vq, dq = voix_off(redac["question"], "question")
        carte(ordre[-1]["fichier_segment"], dossier / "c_question.mp4", redac["question"], "Dis-le en commentaire",
              vq, dq, 3.0)
        morceaux.append(str(dossier / "c_question.mp4"))
    assembler(morceaux, sortie, dmax)

    # contrôle qualité avant d'entrer dans le stock
    noms = {c["fichier_segment"]: c for c in ordre}
    etiquettes = []
    for mc in morceaux:
        nom = Path(mc).stem
        if mc in noms:
            etiquettes.append(f"Clip de {noms[mc]['streamer']} : {noms[mc]['titre']}")
        elif nom == "c_intro":
            etiquettes.append(f"Carte d'intro « {gros_intro} »")
        elif nom.startswith("c") and nom[1:].isdigit():
            k = int(nom[1:])
            etiquettes.append(f"Carte d'annonce avant le clip {k + 1} sur {n} ({ordre[k]['streamer']})")
        else:
            etiquettes.append({"avant": "Début du clip (avant la pause)", "c_devine": "Carte « que va-t-il se passer ? »",
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
