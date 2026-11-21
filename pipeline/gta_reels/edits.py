"""Edits GTA 6 : 15 à 25 s des plus beaux plans de jeu (sans webcam), coupes rapides, grosse typo, couleurs
travaillées. Pas de voix ni de sous-titres : le son tendance est ajouté à la main dans TikTok au moment de publier.

1) Pendant le montage des clips, on met de côté les 3 plus beaux plans de chaque bon clip (extraire_plans).
2) Une fois par jour, Claude assemble un edit avec les meilleurs plans de plusieurs streamers (fabriquer_edit).
"""
import datetime as dt
import json
import random
import subprocess
from pathlib import Path

import cv2
import numpy as np

from .commun import DATA, config, db, log
from .recadrage import _images

L = log("edits")
PLANS = DATA / "plans"
SOURCES = DATA / "sources"          # trailers officiels déposés à la main (jamais téléchargés par l'agent)
PLANS_R = DATA / "plans_rockstar"   # plans découpés dans ces trailers (gardés et réutilisés)
DUREE_PLAN = 2.2  # chaque plan mis de côté dure 2,2 s (on en garde 0,8 à 1,8 s dans l'edit)
COULEURS = ("eq=contrast=1.12:saturation=1.3:gamma=0.97,"
            "colorbalance=rs=-0.04:bs=0.07:rh=0.07:bh=-0.05,vignette=PI/5")  # look ciné : ombres bleues, lumières chaudes


def _cadre(camera) -> float:
    """Position gauche (fraction) du cadre vertical 9:16 dans l'image 16:9, au centre mais loin de la webcam."""
    lj = (9 / 16) / (16 / 9)  # 0,316
    x0 = (1 - lj) / 2
    if camera:
        x, y, l, h = camera
        if x < x0 + lj and x + l > x0:  # la webcam déborde dans le cadre : on le pousse de l'autre côté
            x0 = x + l + 0.01 if x + l / 2 < 0.5 else x - lj - 0.01
    return min(max(x0, 0.0), 1 - lj)


def extraire_plans(clip, debut: float, fin: float, coupes, camera, nb: int = 3) -> int:
    """Met de côté les plus beaux plans du clip (de l'action, pas d'écran figé ni noir), au format vertical."""
    source = clip["fichier_brut"]
    x0 = _cadre(camera)
    energies = []
    prec = None
    for t, img in _images(source, debut, fin, 0.25):
        h0, w0 = img.shape[:2]
        zone = img[:, int(x0 * w0):int((x0 + 0.316) * w0)]
        gris = cv2.cvtColor(cv2.resize(zone, (90, 160)), cv2.COLOR_BGR2GRAY)
        if prec is not None:
            _, diff = cv2.threshold(cv2.absdiff(gris, prec), 25, 255, cv2.THRESH_BINARY)
            energies.append((t, diff.mean() / 255, gris.mean()))
        prec = gris
    candidats, secours = [], []
    for i in range(len(energies) - 8):
        fen = energies[i:i + 9]  # 2,25 s
        mouv = float(np.mean([e for _, e, _ in fen]))
        pic = max(e for _, e, _ in fen)
        lum = float(np.mean([b for _, _, b in fen]))
        t = fen[0][0]
        if any(a - 0.3 < t - debut + DUREE_PLAN and t - debut < b + 0.3 for a, b in coupes or []):
            continue  # pas dans un temps mort retiré
        if pic > 0.9 or lum < 40:  # changement de scène brutal (coupe, chargement) ou image trop sombre
            continue
        # ça bouge (caméra qui suit une voiture, action…) sans être un chaos illisible
        (candidats if 0.05 < mouv < 0.75 else secours).append((mouv, t))
    if not candidats:  # clip très calme : on prend quand même ce qui bouge le plus
        candidats = [(m, t) for m, t in secours if m > 0.02]
    candidats.sort(reverse=True)
    gardes = []
    for mouv, t in candidats:
        if len(gardes) >= nb:
            break
        if all(abs(t - g) > 4 for _, g in gardes):
            gardes.append((mouv, t))
    PLANS.mkdir(parents=True, exist_ok=True)
    zoom = f"(1+0.06*t/{DUREE_PLAN})"  # lent zoom avant, effet caméra
    faits = 0
    for k, (mouv, t) in enumerate(gardes):
        sortie = PLANS / f"{clip['id']}_{k}.mp4"
        filtre = (f"crop=ih*9/16:ih:iw*{x0:.4f}:0,scale=1080:1920,"
                  f"scale=w='trunc(1080*{zoom}/2)*2':h='trunc(1920*{zoom}/2)*2':eval=frame,"
                  f"crop=1080:1920:(iw-1080)/2:(ih-1920)/2,{COULEURS},setsar=1,fps=30")
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-t", f"{DUREE_PLAN}",
                            "-i", source, "-vf", filtre, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", str(sortie)],
                           check=True, timeout=300)
            Path(str(sortie) + ".json").write_text(json.dumps({
                "clip": clip["id"], "streamer": clip["streamer"], "login": clip["streamer_login"],
                "titre": clip["titre"] or "", "mouvement": round(float(mouv), 3), "note": clip["score"] or 0,
                "cree": dt.datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False), encoding="utf-8")
            faits += 1
        except Exception as e:
            L.warning("Plan %s non extrait : %s", sortie.name, e)
    if faits:
        L.info("%d beau(x) plan(s) mis de côté pour les edits (%s)", faits, clip["streamer"])
    return faits


def _plans_dispo(dossier: Path = PLANS) -> list[dict]:
    out = []
    for j in dossier.glob("*.mp4.json"):
        f = Path(str(j)[:-5])
        if f.exists():
            out.append(dict(json.loads(j.read_text(encoding="utf-8")), fichier=str(f)))
    return out


def _coupes_scenes(video: str) -> list[float]:
    r = subprocess.run(["ffmpeg", "-i", video, "-vf", "select='gt(scene,0.3)',showinfo", "-an", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=1800)
    return [float(x.split("pts_time:")[1].split()[0]) for x in r.stderr.splitlines() if "pts_time:" in x]


def importer_trailers() -> int:
    """Découpe chaque trailer déposé dans data/sources en plans (un par changement de scène), au format paysage,
    et garde les plus beaux (ni noirs, ni cartons de texte, avec du mouvement)."""
    from .agent_montage import duree
    SOURCES.mkdir(parents=True, exist_ok=True)
    PLANS_R.mkdir(parents=True, exist_ok=True)
    total = 0
    for src in sorted(SOURCES.iterdir()):
        if src.suffix.lower() not in (".mp4", ".mov", ".mkv", ".webm", ".m4v") or Path(str(src) + ".fait").exists():
            continue
        d = duree(str(src))
        bornes = [0.0] + _coupes_scenes(str(src)) + [d]
        faits = 0
        for k, (a, b) in enumerate(zip(bornes, bornes[1:])):
            if b - a < 1.0:
                continue
            t0, t1 = a + 0.12, min(b - 0.08, a + 0.12 + DUREE_PLAN)
            lum, mouv, prec, n = [], [], None, 0
            for _, img in _images(str(src), t0, t1, 0.25):
                h0, w0 = img.shape[:2]
                gris = cv2.cvtColor(cv2.resize(img, (160, 90)), cv2.COLOR_BGR2GRAY)
                bande = gris[12:78]  # sans les éventuelles bandes noires du format cinéma
                lum.append(bande.mean())
                if prec is not None:
                    _, diff = cv2.threshold(cv2.absdiff(bande, prec), 20, 255, cv2.THRESH_BINARY)
                    mouv.append(diff.mean() / 255)
                prec = bande
            if not lum or np.mean(lum) < 35:  # plan noir, fondu ou carton
                continue
            sortie = PLANS_R / f"{src.stem[:40]}_{k:03d}.mp4"
            try:
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t0:.2f}", "-t", f"{t1 - t0:.2f}",
                                "-i", str(src), "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
                                "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30", "-c:v", "libx264", "-preset",
                                "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
                                "-ac", "2", str(sortie)], check=True, timeout=300)
            except Exception as e:
                L.warning("Plan de trailer non extrait : %s", e)
                continue
            Path(str(sortie) + ".json").write_text(json.dumps({
                "clip": f"{src.stem[:40]}_{k:03d}", "source": src.stem, "streamer": "Rockstar Games",
                "login": "", "titre": src.stem, "mouvement": round(float(np.mean(mouv or [0])), 3), "note": 8,
                "duree": round(t1 - t0, 2), "utilisations": 0, "paysage": True,
                "cree": dt.datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
            faits += 1
        Path(str(src) + ".fait").write_text(str(faits), encoding="utf-8")
        L.info("Trailer « %s » : %d plans gardés", src.name, faits)
        total += faits
    return total


SCHEMA = {"type": "object", "properties": {
    "textes": {"type": "array", "items": {"type": "string"},
               "description": "3 ou 4 phrases chocs de 1 à 4 mots, affichées l'une après l'autre en très gros"},
    "legende": {"type": "string", "description": "Légende TikTok courte et accrocheuse (une phrase)"},
    "hashtags": {"type": "array", "items": {"type": "string"}},
    "son": {"type": "string", "description": "Style de son tendance à mettre dessus, en quelques mots"}},
    "required": ["textes", "legende", "hashtags", "son"], "additionalProperties": False}

CONSIGNE = """Tu écris les textes d'un « edit » TikTok GTA 6 : 15 à 25 secondes de beaux plans de jeu enchaînés vite, sur un son tendance, avec quelques mots énormes à l'écran façon affiche (comme « VICE CITY HITS DIFFERENT », « ONLY IN LEONIDA », « ON EST PAS PRÊTS »).
- textes : 3 ou 4 phrases chocs de 1 à 4 mots chacune, en majuscules, qui forment une petite progression (accroche, montée, chute). Français ou anglais (expressions que les gamers utilisent), jamais de faute. Pas de nom de streamer, pas de fausse info sur le jeu, rien d'« officiel ».
- legende : une phrase qui donne envie de commenter.
- hashtags : 3 à 5, sans espaces.
- son : le style de musique qui collerait (ex. « phonk agressif », « drill lente », « son mélancolique ralenti »)."""


def _textes(plans: list[dict]) -> dict:
    from .redaction import _appeler
    titres = "\n".join(f"- {p['streamer']} : {p['titre']}" for p in plans)
    recents = "\n".join(r[0] or "" for r in db().execute(
        "SELECT hook FROM videos WHERE format='edit' ORDER BY id DESC LIMIT 8"))
    try:
        return _appeler(CONSIGNE, f"Titres des clips d'où viennent les plans :\n{titres}\n\n"
                                  f"Textes déjà utilisés (à ne pas répéter) :\n{recents or '(aucun)'}", SCHEMA, 1500)
    except Exception as e:
        L.warning("Textes de l'edit par défaut (%s)", e)
        return {"textes": random.choice([["VICE CITY", "HITS", "DIFFERENT"], ["ON EST", "PAS PRÊTS", "POUR ÇA"],
                                         ["ONLY", "IN LEONIDA"]]),
                "legende": "GTA 6 c'est autre chose 🌴", "hashtags": ["#gta6edit", "#vicecity"],
                "son": "phonk ou son tendance du moment"}


def _choisir_type() -> str | None:
    """« rockstar » (trailers, en paysage) ou « streams » (plans des clips, en vertical), en alternant."""
    r_ok = len(_plans_dispo(PLANS_R)) >= 12
    s_dispo = _plans_dispo(PLANS)
    s_ok = len(s_dispo) >= 12 and len({p["clip"] for p in s_dispo}) >= 4
    dernier = db().execute("SELECT conseil FROM videos WHERE format='edit' ORDER BY id DESC LIMIT 1").fetchone()
    avant = "rockstar" if dernier and (dernier[0] or "").startswith("[rockstar]") else "streams"
    if r_ok and s_ok:
        return "streams" if avant == "rockstar" else "rockstar"
    return "rockstar" if r_ok else "streams" if s_ok else None


def fabriquer_edit(genre: str | None = None) -> bool:
    """Assemble un edit : plans des trailers Rockstar (paysage, comme les edits qui percent) ou des streams."""
    from .agent_montage import ENCODAGE, FONT, TITRE, _lignes_px, duree
    from .controle import verifier

    e = config().get("edits") or {}
    genre = genre or _choisir_type()
    if not genre:
        return False
    paysage = genre == "rockstar"
    W, H = (1920, 1080) if paysage else (1080, 1920)
    dispo = _plans_dispo(PLANS_R if paysage else PLANS)
    if paysage:  # les plans des trailers resservent : les moins utilisés d'abord, dans un ordre varié
        random.shuffle(dispo)
        dispo.sort(key=lambda p: p.get("utilisations", 0))
        dispo = dispo[:40]
        dispo.sort(key=lambda p: p["mouvement"], reverse=True)
    else:  # les meilleurs plans, en alternant les clips (jamais deux plans du même clip à la suite)
        dispo.sort(key=lambda p: p["mouvement"] * (1 + p["note"] / 10), reverse=True)
    cible = float(e.get("duree_s", 18))
    rythme = [1.5, 0.9, 1.2, 0.8, 1.6, 1.0, 0.9, 1.3, 0.8, 1.1, 1.7, 0.9, 1.2, 1.0, 1.8, 0.9, 1.3, 1.1, 1.5, 1.0]
    choisis, durees, dernier = [], [], None
    while dispo and sum(durees) < cible and len(choisis) < len(rythme):
        p = next((x for x in dispo if x["clip"] != dernier), dispo[0])
        dispo.remove(p)
        choisis.append(p)
        durees.append(min(rythme[len(durees)], p.get("duree", DUREE_PLAN) - 0.05))
        dernier = p["clip"]
    total = sum(durees)
    redac = _textes(choisis)
    textes = [t.strip().upper() for t in redac["textes"] if t.strip()][:4] or ["GTA 6"]

    if paysage:  # peu de texte sur les edits de trailers : 2 phrases au plus
        textes = textes[:2]
    dossier = DATA / "final" / f"edit-{dt.datetime.now():%Y%m%d-%H%M}"
    dossier.mkdir(parents=True, exist_ok=True)
    sortie = dossier / "video.mp4"
    entrees, chaine = [], []
    for i, (p, d) in enumerate(zip(choisis, durees)):
        entrees += ["-t", f"{d:.2f}", "-i", p["fichier"]]
        chaine.append(f"[{i}:v]scale={W}:{H},setsar=1,fps=30,trim=duration={d:.2f},setpts=PTS-STARTPTS[v{i}];"
                      f"[{i}:a]atrim=duration={d:.2f},asetpts=PTS-STARTPTS,"
                      f"afade=t=in:d=0.08,afade=t=out:st={max(d - 0.08, 0):.2f}:d=0.08,volume=0.4[a{i}];")
    n = len(choisis)
    graphe = "".join(chaine) + "".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v0][a]"
    # flash blanc très court sur quelques coupes, pour le rythme
    coupes = np.cumsum(durees)[:-1]
    flashs = "+".join(f"between(t,{c:.2f},{c + 0.07:.2f})" for c in coupes[1::3])
    v = "v0"
    if flashs:
        graphe += f";[v0]eq=brightness='if({flashs},0.35,0)':eval=frame[vf]"
        v = "vf"
    # les phrases chocs, une après l'autre, réparties sur la durée (la dernière jusqu'à la fin)
    tranche = total / len(textes)
    for k, texte in enumerate(textes):
        lignes, taille = _lignes_px(texte, 150 if paysage else 190, 1500 if paysage else 940)
        t0, t1 = k * tranche + 0.15, (k + 1) * tranche if k < len(textes) - 1 else total
        y = (H * 0.5 if paysage else 820) - len(lignes) * taille * 0.55
        for j, ligne in enumerate(lignes[:3]):
            f = dossier / f"t{k}_{j}.txt"
            f.write_text(ligne, encoding="utf-8")
            graphe += (f";[{v}]drawtext=fontfile={TITRE}:textfile={f.name}:fontsize={taille}:fontcolor=white:"
                       f"borderw=6:bordercolor=black:shadowcolor=black@0.7:shadowx=6:shadowy=8:"
                       f"x=(w-text_w)/2:y={y + j * taille * 1.05:.0f}:enable='between(t,{t0:.2f},{t1:.2f})'[t{k}_{j}]")
            v = f"t{k}_{j}"
    graphe += (f";[{v}]noise=alls=5:allf=t,drawtext=fontfile={FONT}:text='Bitume Minute':fontsize=34:"
               f"fontcolor=white@0.55:x=(w-text_w)/2:y=h-{70 if paysage else 150}[vfin]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *entrees, "-filter_complex", graphe,
                    "-map", "[vfin]", "-map", "[a]", *ENCODAGE, "-movflags", "+faststart", sortie.name],
                   check=True, cwd=dossier, timeout=900)

    tags, vus = [], set()
    for h in (config()["tiktok"].get("hashtags_fixes") or []) + redac["hashtags"] + ["#gta6edit"]:
        h = "#" + h.strip().lstrip("#").replace(" ", "")
        if len(h) > 1 and h.lower() not in vus:
            vus.add(h.lower())
            tags.append(h)
    credits = ("Rockstar Games (trailer officiel, edit de fan)" if paysage
               else " ".join(sorted({f"twitch.tv/{p['login']}" for p in choisis})))
    legende = f"{redac['legende']}\n\n🎥 Images : {credits}\n{' '.join(tags[:5])}"[:2200]
    ok, problemes = verifier(sortie, [str(sortie)], [], "edit",
                             {"hook": " / ".join(textes), "legende_complete": legende, "sans_voix": True})
    conn = db()
    conn.execute(
        "INSERT INTO videos (fichier, clips, score, hook, legende, format, duree, statut, erreur, conseil) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (str(sortie), ",".join(sorted({p["clip"] for p in choisis})), 7.5, " ".join(textes), legende, "edit",
         duree(str(sortie)), "prete" if ok else "rejetee", " | ".join(problemes)[:500] or None,
         f"[{genre}] {redac['son']}"))
    conn.commit()
    for p in choisis:
        if paysage:  # plan de trailer : on note qu'il a servi (il resservira plus tard, mélangé autrement)
            meta = {k: v for k, v in p.items() if k != "fichier"}
            meta["utilisations"] = meta.get("utilisations", 0) + 1
            Path(p["fichier"] + ".json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        else:  # plan de stream : ne sert qu'une fois
            Path(p["fichier"]).unlink(missing_ok=True)
            Path(p["fichier"] + ".json").unlink(missing_ok=True)
    L.info("Edit %s : %d plans, %.0f s, %s", "prêt" if ok else "refusé par le contrôle qualité", n, total,
           " / ".join(textes) if ok else " | ".join(problemes))
    return ok


def peut_faire_un_edit() -> bool:
    e = config().get("edits") or {}
    if not e.get("actif", True):
        return False
    par_jour = int(e.get("par_jour", 1))
    faits = db().execute("SELECT COUNT(*) FROM videos WHERE format='edit' AND statut IN ('prete','publiee') "
                         "AND creee_le >= datetime('now','-24 hours')").fetchone()[0]
    return faits < par_jour


def nettoyer(jours: int = 3) -> None:
    limite = dt.datetime.now().timestamp() - jours * 86400
    for f in PLANS.glob("*.mp4"):
        if f.stat().st_mtime < limite:
            f.unlink(missing_ok=True)
            Path(str(f) + ".json").unlink(missing_ok=True)


if __name__ == "__main__":
    print("Edit fabriqué" if fabriquer_edit() else "Pas assez de plans pour un edit (il en faut 12, de 4 clips)")
