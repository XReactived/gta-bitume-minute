"""Edits GTA 6 : 15 à 25 s des plus beaux plans de jeu (sans webcam), coupes rapides, grosse typo, couleurs
travaillées. Pas de voix ni de sous-titres : le son tendance est ajouté à la main dans TikTok au moment de publier.

1) Pendant le montage des clips, on met de côté les 3 plus beaux plans de chaque bon clip (extraire_plans).
2) Une fois par jour, Claude assemble un edit avec les meilleurs plans de plusieurs streamers (fabriquer_edit).
"""
import datetime as dt
import json
import hashlib
import random
import shutil
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
SONS = DATA / "sons"                # sons tendance déposés à la main (mp3) : l'edit est coupé pile sur leur beat
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


def _zone(camera) -> tuple[float, float, float]:
    """Le plus grand cadre 16:9 de l'image qui évite la webcam : (x, y, taille), en fractions de l'image.
    On garde le format paysage (comme les edits qui percent) : beaucoup moins d'agrandissement qu'un recadrage
    vertical, donc une image bien plus nette."""
    if not camera:
        return 0.0, 0.0, 1.0
    x, y, l, h = camera
    options = [  # (taille, x, y) : sous la webcam, au-dessus, à sa droite, à sa gauche
        (1 - (y + h), None, y + h), (y, None, 0.0), (1 - (x + l), x + l, None), (x, 0.0, None)]
    t, x0, y0 = max(options, key=lambda o: o[0])
    t = max(min(t, 1.0), 0.5)  # webcam énorme ou mal détectée : on garde au moins la moitié de l'image
    x0 = (1 - t) / 2 if x0 is None else min(x0, 1 - t)  # centré dans l'espace libre
    y0 = (1 - t) / 2 if y0 is None else min(y0, 1 - t)
    return x0, y0, t


NETTETE = "hqdn3d=1.5:1.5:4:4,scale=1920:1080:flags=lanczos,cas=0.5"  # nettoie la compression Twitch, puis affine


def extraire_plans(clip, debut: float, fin: float, coupes, camera, nb: int = 3) -> int:
    """Met de côté les plus beaux plans du clip (de l'action, pas d'écran figé ni noir), au format vertical."""
    source = clip["fichier_brut"]
    x0, y0, taille = _zone(camera)
    energies = []
    prec = None
    for t, img in _images(source, debut, fin, 0.25):
        h0, w0 = img.shape[:2]
        zone = img[int(y0 * h0):int((y0 + taille) * h0), int(x0 * w0):int((x0 + taille) * w0)]
        gris = cv2.cvtColor(cv2.resize(zone, (160, 90)), cv2.COLOR_BGR2GRAY)
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
        filtre = (f"crop=trunc(iw*{taille:.4f}/2)*2:trunc(ih*{taille:.4f}/2)*2:iw*{x0:.4f}:ih*{y0:.4f},{NETTETE},"
                  f"scale=w='trunc(1920*{zoom}/2)*2':h='trunc(1080*{zoom}/2)*2':eval=frame:flags=lanczos,"
                  f"crop=1920:1080:(iw-1920)/2:(ih-1080)/2,{COULEURS},setsar=1,fps=30")
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-t", f"{DUREE_PLAN}",
                            "-i", source, "-vf", filtre, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", str(sortie)],
                           check=True, timeout=300)
            Path(str(sortie) + ".json").write_text(json.dumps({
                "clip": clip["id"], "streamer": clip["streamer"], "login": clip["streamer_login"],
                "titre": clip["titre"] or "", "mouvement": round(float(mouv), 3), "note": clip["score"] or 0,
                "paysage": True,
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


IMAGES = (".jpg", ".jpeg", ".png", ".webp")


def _id(src: Path) -> str:
    """Nom court et unique d'une source (deux fichiers du même nom dans deux dossiers ne se marchent pas dessus)."""
    return f"{src.stem[:32]}_{hashlib.md5(str(src.relative_to(SOURCES)).encode()).hexdigest()[:6]}"


def _importer_image(src: Path, jeu: str) -> int:
    """Une image officielle (artwork, capture) devient un plan de 2,2 s : zoom lent et léger glissement, comme un
    travelling de caméra. Les images trop petites ou trop sombres sont ignorées."""
    img = cv2.imread(str(src))
    Path(str(src) + ".fait").write_text("0", encoding="utf-8")
    if img is None or img.shape[1] < 960 or img.shape[0] < 540:
        return 0
    if cv2.cvtColor(cv2.resize(img, (160, 90)), cv2.COLOR_BGR2GRAY).mean() < 35:
        return 0
    nom = f"{jeu}_img_{_id(src)}"
    sortie = PLANS_R / f"{nom}.mp4"
    sens = random.choice([(0.2, 0.6), (0.6, 0.2)])  # glisse vers la droite ou vers la gauche
    z = f"(1+0.10*t/{DUREE_PLAN})"
    filtre = ("scale=2208:1242:force_original_aspect_ratio=increase,crop=2208:1242,"
              f"scale=w='trunc(2208*{z}/2)*2':h='trunc(1242*{z}/2)*2':eval=frame,"
              f"crop=1920:1080:'(iw-1920)*({sens[0]}+{sens[1] - sens[0]}*t/{DUREE_PLAN})':'(ih-1080)/2',"
              "setsar=1,fps=30,format=yuv420p")
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", f"{DUREE_PLAN}", "-i", str(src),
                        "-f", "lavfi", "-t", f"{DUREE_PLAN}", "-i", "anullsrc=r=48000:cl=stereo", "-vf", filtre,
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-shortest",
                        str(sortie)], check=True, timeout=300)
    except Exception as e:
        L.warning("Image %s non importée : %s", src.name, e)
        return 0
    Path(str(sortie) + ".json").write_text(json.dumps({
        "clip": nom, "source": src.stem, "jeu": jeu, "streamer": "Rockstar Games", "login": "", "titre": src.stem,
        "mouvement": round(random.uniform(0.15, 0.3), 3), "note": 8, "duree": DUREE_PLAN, "utilisations": 0,
        "paysage": True, "image": True, "cree": dt.datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    return 1


def importer_trailers() -> int:
    """Découpe chaque trailer déposé dans data/sources en plans (un par changement de scène), au format paysage,
    et garde les plus beaux (ni noirs, ni cartons de texte, avec du mouvement)."""
    from .agent_montage import duree
    SOURCES.mkdir(parents=True, exist_ok=True)
    PLANS_R.mkdir(parents=True, exist_ok=True)
    for sous in ("gta5", "gta6"):
        (SOURCES / sous).mkdir(exist_ok=True)
    # tout ce qui est dans sources (sous-dossiers compris) est du GTA VI, sauf le dossier sources/gta5
    fichiers = [(f, "gta5" if (SOURCES / "gta5") in f.parents else "gta6") for f in sorted(SOURCES.rglob("*"))]
    total = 0
    for src, jeu in fichiers:
        if not src.is_file() or Path(str(src) + ".fait").exists():
            continue
        if src.suffix.lower() in IMAGES:
            total += _importer_image(src, jeu)
            continue
        if src.suffix.lower() not in (".mp4", ".mov", ".mkv", ".webm", ".m4v"):
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
            nom = f"{jeu}_{_id(src)}_{k:03d}"
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


def _attaques(f: Path, sr: int = 22050, hop: int = 256) -> tuple[np.ndarray, np.ndarray, float] | None:
    """Courbe des attaques (coups de batterie, notes) et volume, ~86 mesures par seconde."""
    try:
        brut = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(f), "-t", "120", "-ac", "1", "-ar", str(sr),
                               "-f", "s16le", "-"], capture_output=True, check=True, timeout=120).stdout
    except Exception as e:
        L.warning("Son %s illisible : %s", f.name, e)
        return None
    x = np.frombuffer(brut, dtype=np.int16).astype(np.float32) / 32768
    n = len(x) // hop
    if n < 10 * sr / hop:  # moins de 10 s
        return None
    trames = x[:n * hop].reshape(n, hop)
    spec = np.log1p(10 * np.abs(np.fft.rfft(trames * np.hanning(hop), axis=1)))
    flux = np.concatenate([[0], np.maximum(np.diff(spec, axis=0), 0).sum(axis=1)])
    flux = np.maximum(flux - np.convolve(flux, np.ones(16) / 16, "same"), 0)  # garde les pics, pas le fond
    flux /= flux.std() + 1e-9
    return flux, np.sqrt((trames ** 2).mean(axis=1)), sr / hop


def _caler(flux: np.ndarray, fps: float, bpm0: float, t0: float, t1: float) -> tuple[float, float, float]:
    """Cherche le tempo exact (±3 %) et le placement des temps qui tombent le mieux sur les attaques entre t0 et t1.
    Renvoie (bpm, premier temps, part des attaques fortes qui tombent sur un temps ou un demi-temps)."""
    lisse = np.maximum.reduce([np.roll(flux, k) for k in (-1, 0, 1)])  # tolérance ±12 ms
    meilleur = (-1.0, bpm0, 0.0)
    for bpm in bpm0 * (1 + np.linspace(-0.03, 0.03, 61)):
        periode = 60 / bpm
        for ph in np.arange(0, periode, 1 / fps):
            temps = np.arange(t0 + ph, t1, periode)
            idx = np.round(temps * fps).astype(int)
            idx = idx[idx < len(lisse)]
            sc = lisse[idx].mean() if len(idx) else 0
            if sc > meilleur[0]:
                meilleur = (sc, bpm, t0 + ph)
    _, bpm, premier = meilleur
    # contrôle : les attaques fortes de la fenêtre tombent-elles sur la grille (temps ou demi-temps, ±50 ms) ?
    a, b = int(t0 * fps), min(int(t1 * fps), len(flux) - 1)
    seg = flux[a:b]
    pics = [a + i for i in range(1, len(seg) - 1) if seg[i] > seg[i - 1] and seg[i] >= seg[i + 1]
            and seg[i] > np.percentile(seg, 95)]
    demi = 30 / bpm
    ecarts = [abs(((p / fps - premier) / demi + 0.5) % 1 - 0.5) * demi for p in pics]
    return float(bpm), float(premier), float(np.mean([e < 0.05 for e in ecarts])) if ecarts else 0.0


def _analyser_son(f: Path) -> dict | None:
    """Tempo, « drop » (moment où le son explose) et calage précis des temps sur les 20 s qui suivent le drop,
    gardés dans un .json à côté du son."""
    meta = Path(str(f) + ".json")
    if meta.exists():
        return json.loads(meta.read_text(encoding="utf-8"))
    r = _attaques(f)
    if r is None:
        return None
    flux, rms, fps = r
    n = len(flux)
    duree_s = n / fps
    # tempo global : autocorrélation des attaques entre 70 et 180 BPM, ramené entre 90 et 160 (rythme des coupes)
    ac = np.correlate(flux, flux, "full")[n - 1:]
    lags = np.arange(int(fps * 60 / 180), int(fps * 60 / 70) + 1)
    bpm = 60 * fps / lags[np.argmax(ac[lags])]
    while bpm < 90:
        bpm *= 2
    while bpm > 160:
        bpm /= 2
    # drop : le moment où l'énergie monte le plus (pas dans les 2 premières secondes, assez de son derrière)
    def energie(t0, t1):
        a_, b_ = int(t0 * fps), int(t1 * fps)
        return float(rms[max(a_, 0):max(b_, a_ + 1)].mean()) if a_ < n else 0.0
    fin = max(duree_s - 20, 2.5)  # il faut ~20 s de son après le départ (durée de l'edit)
    candidats = [(energie(t, t + 2) - energie(t - 2, t), t) for t in np.arange(2, fin, 0.25)]
    drop = max(candidats)[1] if candidats else 0.0
    t0 = max(0.0, drop - 0.5)
    bpm, premier, calage = _caler(flux, fps, bpm, t0, min(duree_s, t0 + 22))
    d = {"bpm": round(bpm, 2), "drop": round(premier, 3), "calage": round(calage, 2), "duree": round(duree_s, 1),
         "utilisations": 0}
    meta.write_text(json.dumps(d), encoding="utf-8")
    L.info("Son « %s » : %.1f BPM, départ à %.2f s, %.0f %% des attaques sur le beat", f.stem, bpm, premier,
           calage * 100)
    return d


def _choisir_son() -> tuple[Path, dict] | None:
    """Le son déposé le moins utilisé (au hasard entre égaux)."""
    SONS.mkdir(parents=True, exist_ok=True)
    sons = [(f, _analyser_son(f)) for f in SONS.iterdir()
            if f.suffix.lower() in (".mp3", ".m4a", ".wav", ".aac", ".ogg", ".mp4")]
    sons = [(f, m) for f, m in sons if m]
    if not sons:
        return None
    random.shuffle(sons)
    return min(sons, key=lambda s: s[1].get("utilisations", 0))


def _noter_son(f: Path, meta: dict) -> None:
    meta = dict(meta, utilisations=meta.get("utilisations", 0) + 1)
    Path(str(f) + ".json").write_text(json.dumps(meta), encoding="utf-8")


def _mmss(t: float) -> str:
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def _tempo() -> tuple[float, float, str, tuple | None]:
    """Tempo de l'edit, décalage du premier temps, et consigne de son pour la notif : un son déposé (calage exact)
    sinon un BPM au hasard. Le son démarre à une seconde ronde (réglable dans TikTok) : le premier temps tombe
    donc un peu après le début de la vidéo, et les coupes suivent."""
    son = _choisir_son()
    if son:
        f, m = son
        depart = int(m["drop"])
        return m["bpm"], m["drop"] - depart, f"le son « {f.stem.strip()} » en le faisant démarrer à {_mmss(depart)}", son
    return random.choice(BPM), 0.0, "", None


def _grille(bpm: float, motif: list[int], cible: float, maxi: float = 2.0,
            decalage: float = 0.0) -> tuple[list[float], list[int]]:
    """Durées des plans calées sur les temps du son (arrondies à l'image, sans dérive), et le nombre de temps
    écoulés à chaque coupe (pour poser flashs et textes sur les temps forts)."""
    temps = 60 / bpm
    durees, cumuls, cumul, avant = [], [], 0, 0.0
    for nb in motif:
        cumul += max(1, min(nb, int((maxi - (decalage if not durees else 0)) / temps)))
        fin = round((decalage + cumul * temps) * 30) / 30
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
        img_ok = sum(1 for c in choisis if c.get("image")) < (len(choisis) + 1) // 3  # au plus 1 image sur 3 plans
        bon = [x for x in dispo if x.get("duree", DUREE_PLAN) >= 0.5 * d + 0.08 and (img_ok or not x.get("image"))]
        p = next((x for x in bon if x["clip"] != dernier), None) or next(iter(bon), None)
        if not p:
            break
        dispo.remove(p)
        choisis.append(p)
        dernier = p["clip"]
    return choisis


def fabriquer_edit(genre: str | None = None) -> bool:
    """Fabrique plusieurs versions d'un edit (plans, effets, rythme différents), fait noter les versions par
    Claude comme un monteur exigeant, et ne garde la meilleure que si elle est vraiment bonne."""
    e = config().get("edits") or {}
    genre = genre or _choisir_type()
    if not genre:
        return False
    essais, note_min = int(e.get("essais", 3)), float(e.get("note_min", 8))
    versions = []
    for k in range(essais):
        try:
            v = _fabriquer_comparatif(k) if genre == "comparatif" else _fabriquer_simple(genre, k)
        except Exception as ex:
            L.warning("Version %d de l'edit %s ratée : %s", k + 1, genre, ex)
            v = None
        if v:
            versions.append(v)
    if not versions:
        return False
    gagnante, note, avis = _departager(versions)
    for v in versions:
        if v is not gagnante:
            shutil.rmtree(v["dossier"], ignore_errors=True)
    rate = DATA / "edit_rate.json"
    try:
        echecs = json.loads(rate.read_text())["echecs"] if rate.exists() else 0
    except Exception:
        echecs = 0
    if echecs >= 2:  # déjà deux tournées sans version assez bonne : on accepte une très bonne (7)
        note_min = min(note_min, 7.0)
    if note < note_min:
        rate.write_text(json.dumps({"echecs": echecs + 1, "quand": dt.datetime.now().timestamp()}))
        L.info("Edit %s : meilleure version notée %.1f/10 seulement (%s), on réessaie au prochain passage",
               genre, note, avis)
        shutil.rmtree(gagnante["dossier"], ignore_errors=True)
        return False
    rate.unlink(missing_ok=True)
    _enregistrer(gagnante, note, avis)
    return True


def _fabriquer_simple(genre: str, essai: int = 0) -> dict | None:
    """Une version d'edit en plein écran : GTA VI ou GTA V/Online (paysage) ou plans de streams (vertical)."""
    e = config().get("edits") or {}
    officiel = genre in ("rockstar", "online")
    # les plans de streams sont en paysage depuis qu'on évite la webcam sans recadrer en vertical (plus net) ;
    # les anciens plans verticaux encore en réserve donnent un dernier edit vertical
    paysage = officiel or all(p.get("paysage") for p in _plans_dispo(PLANS))
    W, H = (1920, 1080) if paysage else (1080, 1920)
    dispo = _plans_jeu("gta5" if genre == "online" else "gta6") if officiel else _plans_dispo(PLANS)
    if officiel:
        dispo = _moins_utilises(dispo)
        dispo.sort(key=lambda p: p["mouvement"], reverse=True)
    else:  # les meilleurs plans, en alternant les clips (jamais deux plans du même clip à la suite)
        dispo.sort(key=lambda p: p["mouvement"] * (1 + p["note"] / 10), reverse=True)
    if essai:  # autres versions : d'autres plans parmi les bons, dans un autre ordre
        tete = dispo[:30]
        random.shuffle(tete)
        dispo = tete + dispo[30:]
    cible = float(e.get("duree_s", 18))
    bpm, decalage, consigne, son = _tempo()
    durees, cumuls = _grille(bpm, MOTIFS["rapide"], cible, decalage=decalage)
    choisis = _placer(dispo, durees)
    durees, cumuls = durees[:len(choisis)], cumuls[:len(choisis)]
    if len(choisis) < 6:
        return None
    redac = _textes(choisis, genre)
    redac["son"] = consigne or f"{redac['son']}, vers {bpm:.0f} BPM"
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
    return _finir(genre, choisis, durees, graphe, entrees, textes, redac, paysage, W, H, dossier, sortie, cumuls,
                  son=son)


def _finir(genre, choisis, durees, graphe, entrees, textes, redac, paysage, W, H, dossier, sortie, cumuls,
           y_texte=None, son=None) -> dict | None:
    """Flashs, phrases chocs, filigrane, encodage et contrôle qualité d'une version (commun à tous les edits)."""
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
                    "-map", "[vfin]", "-map", "[a]",
                    # qualité plus haute que les clips : TikTok recompresse, il faut lui donner un maximum de détails
                    *[("17" if x == "20" else x) for x in ENCODAGE], "-maxrate", "16M", "-bufsize", "24M", "-movflags", "+faststart", sortie.name],
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
    if not ok:
        L.info("Version d'edit %s refusée par le contrôle qualité : %s", genre, " | ".join(problemes))
        shutil.rmtree(dossier, ignore_errors=True)
        return None
    return {"genre": genre, "sortie": sortie, "dossier": dossier, "choisis": choisis, "textes": textes,
            "legende": legende, "redac": redac, "son": son, "paysage": paysage, "total": total, "n": n}


SCHEMA_JUGE = {"type": "object", "properties": {
    "notes": {"type": "array", "items": {"type": "object", "properties": {
        "version": {"type": "integer"}, "note": {"type": "number"}, "avis": {"type": "string"}},
        "required": ["version", "note", "avis"], "additionalProperties": False}},
    "meilleure": {"type": "integer"}},
    "required": ["notes", "meilleure"], "additionalProperties": False}


def _departager(versions: list[dict]) -> tuple[dict, float, str]:
    """Claude regarde chaque version (une image toutes les ~2 s) et les note comme un monteur d'edits exigeant."""
    import anthropic
    from .commun import secours
    from .controle import _image_et_texte, _mosaique, _modele
    contenu = []
    for k, v in enumerate(versions, 1):
        instants = [v["total"] * (i + 0.5) / 10 for i in range(10)]
        l_, h_ = (384, 216) if v["paysage"] else (216, 384)
        image = _mosaique(str(v["sortie"]), instants, l_, h_, 5)
        contenu += _image_et_texte(image, f"Version {k} : {v['n']} plans, {v['total']:.0f} s, "
                                          f"textes : {' / '.join(v['textes'])}")
    contenu.append({"type": "text", "text": "Note chaque version et choisis la meilleure."})
    try:
        rep = anthropic.Anthropic().beta.messages.create(
            model=_modele(), max_tokens=2000, **secours(_modele()),
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA_JUGE}},
            system=("Tu es un monteur d'« edits » GTA qui font des millions de vues sur TikTok, et tu es très exigeant. "
                    "On te montre plusieurs versions du même edit (10 images de chacune, dans l'ordre). Le son "
                    "tendance sera ajouté ensuite, calé sur le rythme. Note chaque version sur 10, sûr de toi, en "
                    "jugeant : la première image (elle doit arrêter le scroll), la beauté et la variété des plans "
                    "(paysages, action, personnages, véhicules ; pas deux fois la même chose), l'absence de plan "
                    "moche, flou, noir, de menu ou de carton de texte, la lisibilité et l'impact des gros textes, "
                    "et si ça donne envie de regarder jusqu'au bout et de partager. 9-10 : niveau des meilleurs "
                    "edits qui percent. 8 : très bon, on poste. 7 ou moins : pas assez bon, on ne poste pas. "
                    "Donne un avis d'une phrase par version."),
            messages=[{"role": "user", "content": contenu}])
        r = json.loads(next(b.text for b in rep.content if b.type == "text"))
        notes = {n["version"]: n for n in r["notes"]}
        k = r["meilleure"] if 1 <= r["meilleure"] <= len(versions) else max(notes, key=lambda x: notes[x]["note"])
        for i, n in sorted(notes.items()):
            L.info("Edit, version %d : %.1f/10, %s", i, n["note"], n["avis"])
        n = notes.get(k, {"note": 8, "avis": ""})
        return versions[k - 1], float(n["note"]), n["avis"]
    except Exception as e:  # juge indisponible : la première version (déjà validée par le contrôle qualité)
        L.warning("Notation des versions indisponible (%s) : on garde la première", e)
        return versions[0], 8.0, "non notée"


def _enregistrer(v: dict, note: float, avis: str) -> None:
    """Met la version gagnante dans la file de publication et note les plans et le son utilisés."""
    from .agent_montage import duree
    genre, choisis, sortie = v["genre"], v["choisis"], v["sortie"]
    officiel = genre in ("rockstar", "online", "comparatif")
    conn = db()
    conn.execute(
        "INSERT INTO videos (fichier, clips, score, hook, legende, format, duree, statut, erreur, conseil) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (str(sortie), ",".join(sorted({p["clip"] for p in choisis})), note, " ".join(v["textes"]), v["legende"],
         "edit", duree(str(sortie)), "prete", None, f"[{genre}] {v['redac']['son']}"))
    conn.commit()
    for p in choisis:
        if officiel:  # plan officiel : on note qu'il a servi (il resservira plus tard, mélangé autrement)
            meta = {k: x for k, x in p.items() if k != "fichier"}
            meta["utilisations"] = meta.get("utilisations", 0) + 1
            Path(p["fichier"] + ".json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        else:  # plan de stream : ne sert qu'une fois
            Path(p["fichier"]).unlink(missing_ok=True)
            Path(p["fichier"] + ".json").unlink(missing_ok=True)
    if v["son"]:
        _noter_son(*v["son"])
    L.info("Edit %s prêt (%.1f/10, %s) : %d plans, %.1f s, %s", genre, note, avis, v["n"], v["total"],
           " / ".join(v["textes"]))


def _fabriquer_comparatif(essai: int = 0) -> dict | None:
    """GTA V contre GTA VI en écran partagé vertical : GTA V en haut, GTA VI en bas, plan contre plan,
    avec l'étiquette du jeu dans chaque moitié (le format « GTA 5 vs GTA 6 » qui fait débattre en commentaire)."""
    from .agent_montage import TITRE
    e = config().get("edits") or {}
    g5 = _moins_utilises(_plans_jeu("gta5"), 20)
    g6 = _moins_utilises(_plans_jeu("gta6"), 20)
    # les plus beaux plans d'abord (plus de mouvement), mélangés autrement d'une version à l'autre
    for g in (g5, g6):
        g.sort(key=lambda p: p["mouvement"] + random.uniform(0, 0.15 if essai else 0.02), reverse=True)
    cible = float(e.get("duree_s", 18))
    bpm, decalage, consigne, son = _tempo()
    grille, cumuls = _grille(bpm, MOTIFS["lent"], cible, decalage=decalage)  # plus lent : on doit avoir le temps de comparer
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
        return None
    choisis = [p for paire in paires for p in paire]
    redac = _textes(choisis, "comparatif")
    redac["son"] = consigne or f"{redac['son']}, vers {bpm:.0f} BPM"
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
                  cumuls, y_texte=960, son=son)


def peut_faire_un_edit() -> bool:
    e = config().get("edits") or {}
    if not e.get("actif", True):
        return False
    try:  # après une tournée ratée, on attend 2 h avant de refaire des versions (le serveur monte aussi les clips)
        r = json.loads((DATA / "edit_rate.json").read_text())
        if dt.datetime.now().timestamp() - r["quand"] < 7200:
            return False
    except Exception:
        pass
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
