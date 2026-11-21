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
SOURCES = DATA / "sources"          # vidéos officielles déposées à la main (jamais téléchargées par l'agent) :
                                    # GTA VI à la racine ou dans sources/gta6, GTA V et GTA Online dans sources/gta5
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


def _plans_jeu(jeu: str) -> list[dict]:
    """Plans officiels d'un jeu (« gta6 » ou « gta5 ») ; les anciens plans sans étiquette sont du GTA VI."""
    return [p for p in _plans_dispo(PLANS_R) if p.get("jeu", "gta6") == jeu]


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
    for sous in ("gta5", "gta6"):
        (SOURCES / sous).mkdir(exist_ok=True)
    fichiers = [(f, "gta6") for f in sorted(SOURCES.iterdir())] + \
               [(f, j) for j in ("gta6", "gta5") for f in sorted((SOURCES / j).iterdir())]
    total = 0
    for src, jeu in fichiers:
        if not src.is_file() or src.suffix.lower() not in (".mp4", ".mov", ".mkv", ".webm", ".m4v") \
                or Path(str(src) + ".fait").exists():
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
            nom = f"{jeu}_{src.stem[:40]}_{k:03d}"
            sortie = PLANS_R / f"{nom}.mp4"
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
                "clip": nom, "source": src.stem, "jeu": jeu, "streamer": "Rockstar Games",
                "login": "", "titre": src.stem, "mouvement": round(float(np.mean(mouv or [0])), 3), "note": 8,
                "duree": round(t1 - t0, 2), "utilisations": 0, "paysage": True,
                "cree": dt.datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
            faits += 1
        Path(str(src) + ".fait").write_text(str(faits), encoding="utf-8")
        L.info("Vidéo officielle %s « %s » : %d plans gardés", jeu.upper(), src.name, faits)
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


GENRES = {
    "rockstar": "Plans : images officielles de GTA 6 (Vice City, Leonida).",
    "online": "Plans : images officielles de GTA V et GTA Online (Los Santos). L'edit rend hommage à GTA 5 / GTA Online "
              "(nostalgie, « 10 ans après toujours là », « en attendant GTA 6 »). Ne dis pas que ces images sont de GTA 6.",
    "comparatif": "Edit COMPARATIF en écran partagé : GTA V en haut, GTA VI en bas, plan contre plan. Les textes jouent sur "
                  "le fossé entre les deux (ex. « 2013 VS 2026 », « LA DIFFÉRENCE », « C'EST PLUS LE MÊME JEU »). "
                  "Légende qui pousse à débattre en commentaire (« lequel tu préfères ? »).",
    "streams": "Plans : moments de stream de joueurs sur GTA.",
}
DEFAUTS = {
    "online": {"textes": ["LOS SANTOS", "TOUJOURS", "LÀ"], "legende": "10 ans après, toujours le même frisson 🌆",
               "hashtags": ["#gtaonline", "#gta5", "#gtaedit"], "son": "son mélancolique ralenti"},
    "comparatif": {"textes": ["GTA 5 VS GTA 6", "LA DIFFÉRENCE", "EST FOLLE"],
                   "legende": "13 ans d'écart… tu préfères lequel ? 👇",
                   "hashtags": ["#gta5vsgta6", "#gta6", "#gta5"], "son": "phonk qui monte"},
}


def _textes(plans: list[dict], genre: str = "streams") -> dict:
    from .redaction import _appeler
    titres = GENRES.get(genre, "") + "\n" + "\n".join(f"- {p['streamer']} : {p['titre']}" for p in plans)
    recents = "\n".join(r[0] or "" for r in db().execute(
        "SELECT hook FROM videos WHERE format='edit' ORDER BY id DESC LIMIT 8"))
    try:
        return _appeler(CONSIGNE, f"Titres des clips d'où viennent les plans :\n{titres}\n\n"
                                  f"Textes déjà utilisés (à ne pas répéter) :\n{recents or '(aucun)'}", SCHEMA, 1500)
    except Exception as e:
        L.warning("Textes de l'edit par défaut (%s)", e)
        if genre in DEFAUTS:
            return dict(DEFAUTS[genre])
        return {"textes": random.choice([["VICE CITY", "HITS", "DIFFERENT"], ["ON EST", "PAS PRÊTS", "POUR ÇA"],
                                         ["ONLY", "IN LEONIDA"]]),
                "legende": "GTA 6 c'est autre chose 🌴", "hashtags": ["#gta6edit", "#vicecity"],
                "son": "phonk ou son tendance du moment"}


ORDRE = ["comparatif", "rockstar", "online", "streams"]  # rotation : un genre différent chaque jour


def _choisir_type() -> str | None:
    """Le genre suivant dans la rotation, parmi ceux qui ont assez de plans :
    « comparatif » (GTA V contre GTA VI, écran partagé), « rockstar » (GTA VI, paysage),
    « online » (GTA V / Online, paysage), « streams » (plans des clips, vertical)."""
    g6, g5 = len(_plans_jeu("gta6")), len(_plans_jeu("gta5"))
    s_dispo = _plans_dispo(PLANS)
    ok = {"comparatif": g6 >= 6 and g5 >= 6, "rockstar": g6 >= 12, "online": g5 >= 12,
          "streams": len(s_dispo) >= 12 and len({p["clip"] for p in s_dispo}) >= 4}
    dernier = db().execute("SELECT conseil FROM videos WHERE format='edit' ORDER BY id DESC LIMIT 1").fetchone()
    avant = next((g for g in ORDRE if dernier and (dernier[0] or "").startswith(f"[{g}]")), ORDRE[-1])
    i = ORDRE.index(avant)
    return next((g for g in ORDRE[i + 1:] + ORDRE[:i + 1] if ok[g]), None)


def _moins_utilises(plans: list[dict], n: int = 40) -> list[dict]:
    """Les plans officiels resservent : les moins utilisés d'abord, dans un ordre varié."""
    random.shuffle(plans)
    plans.sort(key=lambda p: p.get("utilisations", 0))
    return plans[:n]


BPM = [120, 128, 140, 150]  # tempos des sons d'edit qui tournent (drill, funk, phonk)
MOTIFS = {"rapide": [2, 1, 1, 2, 2, 4, 1, 1, 2, 2, 2, 1, 1, 4, 2, 2, 1, 1, 2, 4, 2, 2, 1, 1, 2, 4],
          "lent": [4, 4, 3, 4, 4, 3, 4, 4, 4, 3, 4, 4, 4, 4]}


def _grille(bpm: int, motif: list[int], cible: float, maxi: float = 2.0) -> tuple[list[float], list[int]]:
    """Durées des plans calées sur les temps du son (arrondies à l'image, sans dérive), et le nombre de temps
    écoulés à chaque coupe (pour poser flashs et textes sur les temps forts)."""
    temps = 60 / bpm
    durees, cumuls, cumul, avant = [], [], 0, 0.0
    for nb in motif:
        cumul += max(1, min(nb, int(maxi / temps)))
        fin = round(cumul * temps * 30) / 30
        durees.append(fin - avant)
        cumuls.append(cumul)
        avant = fin
        if avant >= cible:
            break
    return durees, cumuls


def _effet(d: float, src: float, k: int) -> tuple[str, str]:
    """Vitesse (normal, ralenti, rampe : accéléré puis ralenti au moment fort) et mouvement de caméra
    (punch : zoom qui claque sur la coupe, secousse, lent : zoom avant doux) de chaque plan."""
    r = random.random()
    if src >= 1.02 * d + 0.05 and r < 0.35:
        vit = "rampe"
    elif src < d + 0.05 or (k % 5 == 4 and r < 0.6):
        vit = "ralenti"
    else:
        vit = "normal"
    return vit, random.choice(["punch", "punch", "secousse", "lent"])


def _chaine(entree: str, sortie: str, d: float, W: int, H: int, vit: str, mouv: str) -> str:
    """Filtre d'un plan : cadrage W×H, vitesse, coupe à la durée d, puis mouvement de caméra."""
    if vit == "ralenti":
        pts = "setpts=2*PTS,"
    elif vit == "rampe":  # 40 % du plan en accéléré ×1,8, puis ralenti ×0,5
        x = 0.4 * d * 1.8
        pts = f"setpts='if(lt(T,{x:.3f}),PTS/1.8,PTS/0.5+{x / 1.8 - x / 0.5:.4f}/TB)',"
    else:
        pts = ""
    z = {"punch": "(1+0.16*exp(-t*11))", "lent": f"(1+0.07*t/{d:.2f})"}.get(mouv)
    if z:
        fx = (f"scale=w='trunc({W}*{z}/2)*2':h='trunc({H}*{z}/2)*2':eval=frame,"
              f"crop={W}:{H}:(iw-{W})/2:(ih-{H})/2")
    else:  # secousse qui s'amortit
        fx = (f"scale={int(W * 1.07) // 2 * 2}:{int(H * 1.07) // 2 * 2},"
              f"crop={W}:{H}:'(iw-ow)/2*(1+0.9*sin(t*83)*exp(-t*5))':'(ih-oh)/2*(1+0.9*cos(t*67)*exp(-t*5))'")
    return (f"[{entree}]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,{pts}fps=30,"
            f"trim=duration={d:.3f},setpts=PTS-STARTPTS,{fx},setsar=1[{sortie}];")


def _son(entree: str, sortie: str, d: float) -> str:
    return (f"[{entree}]atrim=duration={d:.3f},asetpts=PTS-STARTPTS,apad=whole_dur={d:.3f},"
            f"afade=t=in:d=0.08,afade=t=out:st={max(d - 0.08, 0):.3f}:d=0.08,volume=0.4[{sortie}];")


def _placer(dispo: list[dict], durees: list[float]) -> list[dict]:
    """Un plan par case de la grille, assez long, jamais deux plans du même clip à la suite."""
    choisis, dernier = [], None
    for d in durees:
        p = next((x for x in dispo if x["clip"] != dernier and x.get("duree", DUREE_PLAN) >= 0.5 * d + 0.08),
                 None) or next((x for x in dispo if x.get("duree", DUREE_PLAN) >= 0.5 * d + 0.08), None)
        if not p:
            break
        dispo.remove(p)
        choisis.append(p)
        dernier = p["clip"]
    return choisis


def fabriquer_edit(genre: str | None = None) -> bool:
    """Assemble un edit : plans officiels (GTA VI, GTA V/Online, ou comparatif) ou plans des streams."""
    e = config().get("edits") or {}
    genre = genre or _choisir_type()
    if not genre:
        return False
    if genre == "comparatif":
        return _fabriquer_comparatif()
    officiel = genre in ("rockstar", "online")
    paysage = officiel
    W, H = (1920, 1080) if paysage else (1080, 1920)
    dispo = _plans_jeu("gta5" if genre == "online" else "gta6") if officiel else _plans_dispo(PLANS)
    if officiel:
        dispo = _moins_utilises(dispo)
        dispo.sort(key=lambda p: p["mouvement"], reverse=True)
    else:  # les meilleurs plans, en alternant les clips (jamais deux plans du même clip à la suite)
        dispo.sort(key=lambda p: p["mouvement"] * (1 + p["note"] / 10), reverse=True)
    cible = float(e.get("duree_s", 18))
    bpm = random.choice(BPM)
    durees, cumuls = _grille(bpm, MOTIFS["rapide"], cible)
    choisis = _placer(dispo, durees)
    durees, cumuls = durees[:len(choisis)], cumuls[:len(choisis)]
    if len(choisis) < 6:
        return False
    redac = _textes(choisis, genre)
    redac["son"] = f"{redac['son']}, vers {bpm} BPM"
    textes = [t.strip().upper() for t in redac["textes"] if t.strip()][:4] or ["GTA 6"]

    if paysage:  # peu de texte sur les edits de trailers : 2 phrases au plus
        textes = textes[:2]
    dossier = DATA / "final" / f"edit-{dt.datetime.now():%Y%m%d-%H%M%S}"
    dossier.mkdir(parents=True, exist_ok=True)
    sortie = dossier / "video.mp4"
    entrees, chaine = [], []
    for i, (p, d) in enumerate(zip(choisis, durees)):
        entrees += ["-i", p["fichier"]]
        vit, mouv = _effet(d, p.get("duree", DUREE_PLAN), i)
        chaine.append(_chaine(f"{i}:v", f"v{i}", d, W, H, vit, mouv) + _son(f"{i}:a", f"a{i}", d))
    n = len(choisis)
    graphe = "".join(chaine) + "".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v0][a]"
    return _finir(genre, choisis, durees, graphe, entrees, textes, redac, paysage, W, H, dossier, sortie, cumuls)


def _finir(genre, choisis, durees, graphe, entrees, textes, redac, paysage, W, H, dossier, sortie, cumuls,
           y_texte=None) -> bool:
    """Flashs, phrases chocs, filigrane, encodage, contrôle qualité et mise en file (commun à tous les edits)."""
    from .agent_montage import ENCODAGE, FONT, TITRE, _lignes_px, duree
    from .controle import verifier
    total, n = sum(durees), len(choisis)
    # flash blanc très court sur quelques coupes, pour le rythme
    coupes = list(np.cumsum(durees)[:-1])
    # flash blanc sur les coupes qui tombent sur un temps fort (début de mesure)
    fortes = [c for c, b in zip(coupes, cumuls) if b % 4 == 0] or coupes[1::3]
    flashs = "+".join(f"between(t,{c:.3f},{c + 0.07:.3f})" for c in fortes)
    v = "v0"
    if flashs:
        graphe += f";[v0]eq=brightness='if({flashs},0.35,0)':eval=frame[vf]"
        v = "vf"
    # les phrases chocs, une après l'autre, réparties sur la durée (la dernière jusqu'à la fin)
    # chaque phrase apparaît pile sur une coupe (donc sur le temps), la dernière reste jusqu'à la fin
    bornes = [0.0] + [min(coupes, key=lambda c: abs(c - k * total / len(textes))) if coupes else k * total / len(textes)
                      for k in range(1, len(textes))] + [total]
    for k, texte in enumerate(textes):
        lignes, taille = _lignes_px(texte, 150 if paysage else 190, 1500 if paysage else 940)
        t0, t1 = bornes[k], bornes[k + 1]
        y = (y_texte or (H * 0.5 if paysage else 820)) - len(lignes) * taille * 0.55
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
    extra = {"online": ["#gtaonline"], "comparatif": ["#gta5vsgta6"]}.get(genre, ["#gta6edit"])
    for h in (config()["tiktok"].get("hashtags_fixes") or []) + redac["hashtags"] + extra:
        h = "#" + h.strip().lstrip("#").replace(" ", "")
        if len(h) > 1 and h.lower() not in vus:
            vus.add(h.lower())
            tags.append(h)
    officiel = genre in ("rockstar", "online", "comparatif")
    credits = ("Rockstar Games (images officielles, edit de fan)" if officiel
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
        if officiel:  # plan officiel : on note qu'il a servi (il resservira plus tard, mélangé autrement)
            meta = {k: v for k, v in p.items() if k != "fichier"}
            meta["utilisations"] = meta.get("utilisations", 0) + 1
            Path(p["fichier"] + ".json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        else:  # plan de stream : ne sert qu'une fois
            Path(p["fichier"]).unlink(missing_ok=True)
            Path(p["fichier"] + ".json").unlink(missing_ok=True)
    L.info("Edit %s %s : %d plans, %.1f s, %s", genre, "prêt" if ok else "refusé par le contrôle qualité", n, total,
           " / ".join(textes) if ok else " | ".join(problemes))
    return ok


def _fabriquer_comparatif() -> bool:
    """GTA V contre GTA VI en écran partagé vertical : GTA V en haut, GTA VI en bas, plan contre plan,
    avec l'étiquette du jeu dans chaque moitié (le format « GTA 5 vs GTA 6 » qui fait débattre en commentaire)."""
    from .agent_montage import TITRE
    e = config().get("edits") or {}
    g5 = _moins_utilises(_plans_jeu("gta5"), 20)
    g6 = _moins_utilises(_plans_jeu("gta6"), 20)
    cible = float(e.get("duree_s", 18))
    bpm = random.choice(BPM)
    grille, cumuls = _grille(bpm, MOTIFS["lent"], cible)  # plus lent : on doit avoir le temps de comparer
    paires, durees = [], []
    for d in grille:
        a = next((x for x in g5 if x.get("duree", DUREE_PLAN) >= 0.5 * d + 0.08), None)
        b = next((x for x in g6 if x.get("duree", DUREE_PLAN) >= 0.5 * d + 0.08), None)
        if not (a and b):
            break
        g5.remove(a)
        g6.remove(b)
        paires.append((a, b))
        durees.append(d)
    cumuls = cumuls[:len(paires)]
    if len(paires) < 4:
        return False
    choisis = [p for paire in paires for p in paire]
    redac = _textes(choisis, "comparatif")
    redac["son"] = f"{redac['son']}, vers {bpm} BPM"
    textes = [t.strip().upper() for t in redac["textes"] if t.strip()][:3] or ["GTA 5 VS GTA 6"]
    dossier = DATA / "final" / f"edit-{dt.datetime.now():%Y%m%d-%H%M%S}"
    dossier.mkdir(parents=True, exist_ok=True)
    sortie = dossier / "video.mp4"
    W, H = 1080, 1920
    entrees, chaine = [], []
    for i, ((a, b), d) in enumerate(zip(paires, durees)):
        entrees += ["-i", a["fichier"], "-i", b["fichier"]]
        h_, b_ = 2 * i, 2 * i + 1
        src = min(a.get("duree", DUREE_PLAN), b.get("duree", DUREE_PLAN))
        vit, mouv = _effet(d, src, i)  # même effet sur les deux moitiés, pour comparer à armes égales
        chaine.append(_chaine(f"{h_}:v", f"h{i}", d, 1080, 960, vit, mouv)
                      + _chaine(f"{b_}:v", f"b{i}", d, 1080, 960, vit, mouv)
                      + f"[h{i}][b{i}]vstack=inputs=2[v{i}];" + _son(f"{b_}:a", f"a{i}", d))
    n = len(paires)
    graphe = "".join(chaine) + "".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[vc][a]"
    # trait noir entre les deux moitiés, et l'étiquette du jeu dans chacune
    graphe += (";[vc]drawbox=x=0:y=954:w=iw:h=12:color=black:t=fill,"
               f"drawtext=fontfile={TITRE}:text='GTA V':fontsize=96:fontcolor=white:borderw=5:bordercolor=black:"
               "x=50:y=50,"
               f"drawtext=fontfile={TITRE}:text='GTA VI':fontsize=96:fontcolor=0xFF5FB4:borderw=5:bordercolor=black:"
               "x=50:y=h-160[v0]")
    return _finir("comparatif", choisis, durees, graphe, entrees, textes, redac, False, W, H, dossier, sortie,
                  cumuls, y_texte=960)


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
