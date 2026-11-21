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
