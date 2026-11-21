"""Recadrage qui suit le sujet : le cadre vertical glisse en douceur vers le visage ou l'action.

Sans carte graphique : opencv (version légère) regarde 4 images par seconde en petite taille.
- un visage bien visible (streamer filmé en plein écran) -> on le garde au centre ;
- sinon, on suit la zone où ça bouge le plus (voiture, bagarre, explosion…).
Le mouvement du cadre est lissé et limité en vitesse, pour ne jamais donner le mal de mer.
"""
import cv2
import numpy as np

from .commun import log

L = log("recadrage")

_visages = None


def _detecteur():
    global _visages
    if _visages is None:
        _visages = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return _visages


def suivre(video: str, debut: float, fin: float | None, largeur_cadre: float,
           masque: tuple[float, float, float, float] | None = None) -> list[tuple[float, float]] | None:
    """Renvoie des repères (temps depuis debut, position du cadre de 0 = tout à gauche à 1 = tout à droite),
    un par seconde, ou None si le cadre centré convient (le sujet reste au milieu).
    largeur_cadre : largeur du cadre vertical en fraction de l'image. masque : zone à ignorer (la webcam)."""
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        return None
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps if fps else 0
    fin = min(fin or total, total) if total else fin
    if not fin or fin - debut < 2:
        return None
    pas = 0.25  # 4 images par seconde
    cibles, prec = [], None
    t = debut
    while t < fin:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, img = cap.read()
        if not ok:
            break
        h0, w0 = img.shape[:2]
        petit = cv2.resize(img, (320, int(320 * h0 / w0)))
        gris = cv2.cvtColor(petit, cv2.COLOR_BGR2GRAY)
        H, W = gris.shape
        if masque:
            x, y, l, h = masque
            gris[int(y * H):int((y + h) * H), int(x * W):int((x + l) * W)] = 0
        cible = None
        visages = _detecteur().detectMultiScale(gris, 1.15, 5, minSize=(int(H * 0.12), int(H * 0.12)))
        if len(visages):
            fx, fy, fw, fh = max(visages, key=lambda v: v[2] * v[3])
            cible = (fx + fw / 2) / W
        elif prec is not None:
            diff = cv2.absdiff(gris, prec)
            diff = cv2.GaussianBlur(diff, (9, 9), 0)
            _, diff = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
            colonnes = diff.sum(axis=0).astype(float)
            energie = colonnes.sum() / (255 * H * W)
            if 0.01 < energie < 0.6:  # un peu de mouvement, mais pas toute l'image (panoramique de caméra)
                cible = float((colonnes * np.arange(W)).sum() / colonnes.sum()) / W
        prec = gris
        cibles.append((t - debut, cible))
        t += pas
    cap.release()
    if len(cibles) < 8:
        return None

    # lissage : on comble les trous, médiane glissante, puis vitesse limitée
    vals, dernier = [], 0.5
    for _, c in cibles:
        dernier = c if c is not None else dernier
        vals.append(dernier)
    vals = np.array(vals)
    med = np.array([np.median(vals[max(0, i - 4):i + 5]) for i in range(len(vals))])
    pos, lisse = min(max(med[0], largeur_cadre / 2), 1 - largeur_cadre / 2), []  # démarre déjà sur le sujet
    vmax = 0.10 * pas  # au plus 10 % de la largeur de l'image par seconde
    for v in med:
        cible = min(max(v, largeur_cadre / 2), 1 - largeur_cadre / 2)
        pos += max(-vmax, min(vmax, (cible - pos) * 0.25))
        lisse.append(pos)
    if max(abs(p - 0.5) for p in lisse) < 0.04:
        return None  # le sujet reste au centre : cadre fixe
    marge = 1 - largeur_cadre
    reperes = []
    for i in range(0, len(lisse), int(1 / pas)):  # un repère par seconde
        gauche = lisse[i] - largeur_cadre / 2
        reperes.append((cibles[i][0], min(max(gauche / marge if marge > 0 else 0.5, 0.0), 1.0)))
    L.info("Cadre qui suit le sujet : %d repères", len(reperes))
    return reperes


def expression(reperes: list[tuple[float, float]]) -> str:
    """Position du cadre (0 à 1) en fonction du temps t, interpolée entre les repères (pour ffmpeg)."""
    expr = f"{reperes[-1][1]:.4f}"
    for (t0, a), (t1, b) in reversed(list(zip(reperes, reperes[1:]))):
        expr = f"if(lt(t\\,{t1:.2f})\\,{a:.4f}+({b - a:.4f})*(t-{t0:.2f})/{max(t1 - t0, 0.01):.2f}\\,{expr})"
    return expr


def temps_morts(video: str, debut: float, fin: float, mots: list[dict],
                masque: tuple[float, float, float, float] | None = None,
                duree_min: float = 2.5) -> list[tuple[float, float]]:
    """Repère au milieu du clip les passages où l'image du jeu est figée (menu pause, chargement, carte)
    ET où personne ne parle. Renvoie les morceaux à retirer (temps depuis debut), du plus tôt au plus tard.
    Prudent : on ne retire que des passages d'au moins duree_min secondes, en laissant 0,4 s de chaque côté."""
    cap = cv2.VideoCapture(video)
    if not cap.isOpened() or fin - debut < 8:
        return []
    pas, t, prec, figees = 0.25, debut, None, []
    while t < fin:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, img = cap.read()
        if not ok:
            break
        h0, w0 = img.shape[:2]
        gris = cv2.cvtColor(cv2.resize(img, (320, int(320 * h0 / w0))), cv2.COLOR_BGR2GRAY)
        H, W = gris.shape
        if masque:  # la webcam bouge tout le temps : on ne regarde que le jeu
            x, y, l, h = masque
            gris[int(y * H):int((y + h) * H), int(x * W):int((x + l) * W)] = 0
        if prec is not None:
            _, diff = cv2.threshold(cv2.absdiff(gris, prec), 18, 255, cv2.THRESH_BINARY)
            figees.append((t - debut, diff.sum() / (255 * H * W) < 0.002))
        prec = gris
        t += pas
    cap.release()
    parole = [(w["t0"] - 0.3, w["t1"] + 0.3) for w in mots]  # temps déjà comptés depuis debut
    morceaux, depart = [], None
    for i, (tt, fige) in enumerate(figees + [(fin - debut, False)]):
        if fige and depart is None:
            depart = tt - pas
        elif not fige and depart is not None:
            a, b = depart + 0.4, tt - 0.4
            # on ne retire jamais un passage où le streamer parle : on le découpe autour des paroles
            for p0, p1 in sorted(parole):
                if p1 <= a or p0 >= b:
                    continue
                if p0 - a >= duree_min:
                    morceaux.append((a, p0))
                a = max(a, p1)
            if b - a >= duree_min:
                morceaux.append((a, b))
            depart = None
    total = sum(b - a for a, b in morceaux)
    if morceaux and (fin - debut - total < 6 or total > 0.5 * (fin - debut)):
        return []  # couperait trop : on garde tout, par prudence
    if morceaux:
        L.info("Temps morts au milieu du clip retirés : %s", ", ".join(f"{a:.1f}-{b:.1f} s" for a, b in morceaux))
    return morceaux


def decaler(t: float, coupes: list[tuple[float, float]]) -> float | None:
    """Temps dans le clip d'origine -> temps après retrait des coupes (None s'il tombe dans une coupe)."""
    retire = 0.0
    for a, b in coupes:
        if t >= b:
            retire += b - a
        elif t > a:
            return None
    return t - retire


def pic_son(video: str, debut: float, fin: float, coupes: list[tuple[float, float]] | None = None,
            mini: float = 3.0) -> float | None:
    """Moment le plus fort du clip (le streamer crie, rigole, explosion…), repéré au volume du son.
    Renvoie le temps dans le segment final (temps morts retirés), ou None s'il n'y a pas de vrai pic.
    mini : on ignore les premières secondes (le clip démarre déjà là-dessus)."""
    import subprocess

    try:
        brut = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{debut:.3f}", "-to", f"{fin:.3f}",
                               "-i", video, "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                              capture_output=True, check=True, timeout=120).stdout
    except Exception:
        return None
    son = np.frombuffer(brut, dtype=np.int16).astype(float)
    fen = 800  # 0,1 s
    if len(son) < fen * 60:
        return None
    rms = np.sqrt(np.convolve(son[: len(son) // fen * fen].reshape(-1, fen).__pow__(2).mean(axis=1),
                              np.ones(5) / 5, mode="same"))  # volume lissé sur 0,5 s
    temps = np.arange(len(rms)) * 0.1
    ok = temps >= mini
    for a, b in coupes or []:
        ok &= (temps < a) | (temps > b)
    if not ok.any():
        return None
    i = int(np.argmax(np.where(ok, rms, -1)))
    if rms[i] < 2.2 * max(np.median(rms), 1.0):  # pas un vrai pic : volume à peu près constant
        return None
    t = decaler(float(temps[i]), coupes or [])
    if t is not None:
        L.info("Moment fort repéré à %.1f s (son %.1f fois plus fort que la moyenne)", t, rms[i] / max(np.median(rms), 1.0))
    return t
