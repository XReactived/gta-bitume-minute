"""Contrôle qualité : chaque vidéo montée est vérifiée avant d'entrer dans le stock.

1. Contrôle technique : la vidéo finale contient bien tous les morceaux (rien de coupé).
2. Contrôle par Claude : il regarde une image de chaque morceau et lit tous les textes, et vérifie que
   l'ensemble est cohérent (bon nombre de clips annoncés, numéros dans l'ordre, textes qui collent aux
   images, pas de sous-titres en double, rien de coupé ou d'illisible).
Une vidéo refusée n'est jamais envoyée sur TikTok.
"""
import base64
import json
import subprocess
import tempfile
from pathlib import Path

import anthropic

from .commun import config, log, secours

L = log("controle")

SCHEMA = {"type": "object", "properties": {
    "ok": {"type": "boolean"},
    "problemes": {"type": "array", "items": {"type": "string"}},
}, "required": ["ok", "problemes"], "additionalProperties": False}


def _duree(f: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip() or 0)


def _mosaique(video: str, instants: list[float], largeur: int = 270, hauteur: int = 480, cols: int = 6) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        for i, t in enumerate(instants):
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1",
                            "-vf", f"scale={largeur}:{hauteur}", f"{d}/{i:02d}.jpg"], check=True, timeout=60)
        cols = min(len(instants), cols)
        lignes = -(-len(instants) // cols)
        sortie = Path(d) / "m.jpg"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "1", "-i", f"{d}/%02d.jpg", "-vf",
                        f"tile={cols}x{lignes}:padding=6:color=white", "-frames:v", "1", "-q:v", "4", str(sortie)],
                       check=True, timeout=60)
        return sortie.read_bytes()


def _modele() -> str:
    t = config()["tiktok"]
    return t.get("modele_controle") or t.get("modele_ia", "claude-opus-5-5")


def _image_et_texte(image: bytes, texte: str) -> list:
    return [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(image).decode()}},
            {"type": "text", "text": texte}]


REGLES_TIKTOK = (
    "Règles TikTok (même dans un jeu vidéo) : refuse toute nudité ou seins/fesses visibles, tout contenu sexuel ou "
    "très suggestif (club de strip-tease, danse érotique, prostitution, scène de sexe), la violence très crue ou "
    "gore (démembrement, torture, sang en gros plan), l'automutilation, la drogue consommée de façon mise en avant, "
    "les symboles haineux. La violence classique de GTA (fusillades, courses-poursuites, accidents, bagarres) est "
    "acceptée."
)


def juger_clip(video: str, duree: float, streamer: str, titre: str,
               transcription: str, langue: str = "fr") -> tuple[bool | None, float, str, float, float]:
    """Claude regarde 8 images et lit ce qui est dit, puis :
    - vérifie les règles TikTok (refus = le clip n'est jamais utilisé),
    - note l'intérêt du clip sur 10 pour un public TikTok (drôle, surprenant, compréhensible sans contexte).
    - repère où commence et finit vraiment le moment, pour couper les temps morts.
    Renvoie (autorisé, note, raison, début, fin) ; autorisé = None si Claude est indisponible (on réessaiera)."""
    instants = [max(duree, 1.0) * (i + 1) / 9 for i in range(8)]
    try:
        image = _mosaique(video, instants, largeur=480, hauteur=270, cols=4)
        rep = anthropic.Anthropic().beta.messages.create(
            model=_modele(), max_tokens=1500, **secours(_modele()),
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": {
                "type": "object", "properties": {
                    "autorise": {"type": "boolean"},
                    "note": {"type": "number", "description": "Intérêt du clip pour TikTok, de 0 à 10"},
                    "raison": {"type": "string", "description": "Une phrase courte (20 mots maximum)"},
                    "debut_s": {"type": "number", "description": "Seconde où garder le début du clip"},
                    "fin_s": {"type": "number", "description": "Seconde où couper la fin du clip"}},
                "required": ["autorise", "note", "raison", "debut_s", "fin_s"], "additionalProperties": False}}},
            messages=[{"role": "user", "content": _image_et_texte(image, (
                f"Voici 8 images d'un clip Twitch de {streamer} sur GTA 6 ({duree:.0f} s), titre « {titre} ».\n"
                f"Ce qui est dit (transcription automatique, avec repères de temps) : {transcription[:1500] or '(rien)'}\n"
                + ("" if langue == "fr" else
                   "Ce streamer parle anglais : notre public est français et verra des sous-titres traduits. "
                   "Note plus sévèrement un clip dont l'intérêt repose sur un long discours ou un jeu de mots "
                   "anglais ; favorise ce qui se comprend à l'image (action, crash, réaction, situation absurde).\n")
                + "\n"
                f"1) Règles : {REGLES_TIKTOK} Au moindre doute sur de la nudité ou du contenu sexuel, autorise = false.\n"
                "2) Note de 0 à 10 l'intérêt de ce clip pour une vidéo TikTok grand public, en étant exigeant : "
                "8-10 = vraiment drôle, surprenant ou spectaculaire, compréhensible sans connaître le streamer ; "
                "5-7 = sympa sans plus ; 0-4 = ennuyeux, sans action, incompréhensible, menu/chargement, streamer qui "
                "parle de tout autre chose que le jeu.\n"
                f"3) Coupe les temps morts (les images sont prises à {', '.join(f'{x:.0f} s' for x in instants)}) : "
                "debut_s = la seconde où garder le début, pour que la vidéo démarre presque directement sur "
                "l'action (garde 1 à 2 s de contexte, 0 si le début est déjà bon) ; fin_s = la seconde où le moment est "
                f"terminé (supprime ce qui traîne après, {duree:.0f} si la fin est bonne). Ne coupe jamais une phrase "
                "ou le moment clé : en cas de doute, garde."))}],
        )
        r = json.loads(next(b.text for b in rep.content if b.type == "text"))
        return (bool(r["autorise"]), float(r["note"]), r["raison"],
                float(r.get("debut_s") or 0), float(r.get("fin_s") or duree))
    except Exception as e:
        L.warning("Jugement du clip impossible (%s) : on réessaiera au prochain passage", e)
        return None, 0.0, str(e)[:300], 0.0, duree


def trouver_camera(video: str, duree: float) -> tuple[float, float, float, float] | None:
    """Claude repère la caméra du streamer (sa tête filmée) incrustée dans le stream.
    Renvoie (x, y, largeur, hauteur) en fractions de l'image, ou None s'il n'y en a pas."""
    try:
        image = _mosaique(video, [max(duree, 1.0) * 0.4], largeur=960, hauteur=540, cols=1)
        rep = anthropic.Anthropic().beta.messages.create(
            model=_modele(), max_tokens=800, **secours(_modele()),
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": {
                "type": "object", "properties": {
                    "camera": {"type": "boolean"},
                    "gauche": {"type": "number"}, "haut": {"type": "number"},
                    "droite": {"type": "number"}, "bas": {"type": "number"}},
                "required": ["camera", "gauche", "haut", "droite", "bas"], "additionalProperties": False}}},
            messages=[{"role": "user", "content": _image_et_texte(image, (
                "Image d'un stream Twitch de GTA 6 (960 x 540 pixels). Y a-t-il une webcam du streamer (son visage "
                "filmé en vrai, pas un personnage du jeu) incrustée dans un rectangle ? Si oui, donne le rectangle "
                "exact de cette webcam en pixels (gauche, haut, droite, bas), bords du cadre compris. Sinon, "
                "camera = false et mets 0 partout."))}],
        )
        r = json.loads(next(b.text for b in rep.content if b.type == "text"))
        if not r["camera"]:
            return None
        x0, y0, x1, y1 = r["gauche"] / 960, r["haut"] / 540, r["droite"] / 960, r["bas"] / 540
        x0, y0, x1, y1 = max(0.0, x0), max(0.0, y0), min(1.0, x1), min(1.0, y1)
        l, h = x1 - x0, y1 - y0
        if l < 0.08 or h < 0.08 or l * h > 0.4:  # trop petit ou trop grand : pas une webcam incrustée
            return None
        return x0, y0, l, h
    except Exception as e:
        L.warning("Repérage de la caméra impossible (%s) : jeu en plein écran", e)
        return None


def verifier(sortie: Path, morceaux: list[str], etiquettes: list[str], fmt: str, redac: dict) -> tuple[bool, list[str]]:
    """Renvoie (ok, problèmes)."""
    durees = [_duree(m) for m in morceaux]
    total, reel = sum(durees), _duree(str(sortie))
    if abs(reel - total) > 1.5:
        return False, [f"vidéo finale de {reel:.0f} s au lieu de {total:.0f} s : un morceau manque ou est coupé"]

    instants, t = [], 0.0
    if len(morceaux) == 1:  # vidéo déjà finie (contrôle du stock) : une image toutes les quelques secondes
        n = 12
        instants = [reel * (i + 0.5) / n for i in range(n)]
        etiquettes, durees = [f"Image à {x:.0f} s" for x in instants], [reel / n] * n
    for e, d in ([] if instants else zip(etiquettes, durees)):
        # un clip avec un texte posé au début : image prise pendant que le texte est affiché
        instants.append(t + (min(1.2, d * 0.5) if "texte posé" in e else d * 0.5))
        t += d
    instants = instants[:24]
    deroule = "\n".join(f"Image {i + 1} — {e} ({d:.0f} s)" for i, (e, d) in enumerate(zip(etiquettes, durees)))
    textes = (f"Format : {fmt}\nTitre à l'écran : {redac.get('hook')}\nVoix-off d'intro : {redac.get('intro')}\n"
              f"Annonces lues : {json.dumps(redac.get('annonces'), ensure_ascii=False)}\n"
              f"Question : {redac.get('question') or '(aucune)'}\nSuite : {redac.get('suite') or '(aucune)'}\n"
              f"Légende TikTok : {redac.get('legende_complete')}")
    try:
        image = _mosaique(str(sortie), instants)
        rep = anthropic.Anthropic().beta.messages.create(
            model=_modele(),
            max_tokens=2000, **secours(_modele()),
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=("Tu es le contrôleur qualité exigeant d'une page TikTok française de moments GTA 6 de streamers. "
                    "Une vidéo ne part que si elle est parfaite."),
            messages=[{"role": "user", "content": [
                *_image_et_texte(image, "")[:1],
                {"type": "text", "text": (
                    f"La mosaïque montre, dans l'ordre (gauche à droite, puis ligne suivante), une image de chaque "
                    f"morceau de la vidéo (au début du morceau quand un texte y est posé, sinon au milieu). "
                    f"Mise en page voulue : image verticale plein écran, soit la webcam du streamer en haut et le jeu "
                    f"en dessous, soit le jeu recadré au centre sur fond flou :\n{deroule}\n\nTextes de la vidéo :\n{textes}\n\n"
                    "Vérifie que tout est cohérent : le nombre de clips annoncé (« les deux meilleurs moments »…) "
                    "correspond au nombre de clips réellement présents ; les numéros d'un top vont bien en compte à "
                    "rebours jusqu'au numéro 1 ; chaque annonce nomme le bon streamer ; le titre, l'intro et la légende "
                    "ne promettent rien que la vidéo ne montre pas ; aucun texte n'est coupé, illisible ou hors de "
                    "l'écran ; pas de sous-titres en double sur un même clip (nos sous-titres jaunes + ceux du "
                    "streamer) ; pas d'image noire ou figée à la place d'un clip ; " + REGLES_TIKTOK + " "
                    "ok = true seulement si tu ne vois aucun problème réel. Liste chaque problème en une phrase courte.")},
            ]}],
        )
        r = json.loads(next(b.text for b in rep.content if b.type == "text"))
        return bool(r["ok"]), list(r["problemes"])
    except Exception as e:  # contrôle IA indisponible : le contrôle technique a déjà réussi
        L.warning("Contrôle IA indisponible (%s) : seul le contrôle technique a été fait", e)
        return True, []


def controler_stock() -> None:
    """Revérifie les vidéos déjà prêtes (montées avant le contrôle qualité) et retire celles qui ne vont pas."""
    from .commun import db
    conn = db()
    for v in conn.execute("SELECT * FROM videos WHERE statut='prete' ORDER BY id").fetchall():
        if not Path(v["fichier"]).exists():
            ok, problemes = False, ["fichier introuvable"]
        elif _duree(v["fichier"]) >= 179.5:  # anciennes vidéos coupées net à 3 min : la fin manque
            ok, problemes = False, ["vidéo coupée à 3 min, la fin manque"]
        elif _duree(v["fichier"]) > float(config()["montage"].get("duree_max_s", 90)) + 10:
            ok, problemes = False, [f"trop longue ({_duree(v['fichier']):.0f} s)"]
        else:
            ok, problemes = verifier(Path(v["fichier"]), [v["fichier"]], [], v["format"] or "top",
                                     {"hook": v["hook"], "legende_complete": v["legende"]})
        if not ok:
            if v["serie"]:
                conn.execute("UPDATE videos SET statut='rejetee', erreur=? WHERE serie=?", (" | ".join(problemes)[:500], v["serie"]))
            else:
                conn.execute("UPDATE videos SET statut='rejetee', erreur=? WHERE id=?", (" | ".join(problemes)[:500], v["id"]))
            conn.commit()
        print(f"Vidéo {v['id']} « {v['hook']} » : {'OK' if ok else 'RETIRÉE — ' + ' | '.join(problemes)}")
    reste = conn.execute("SELECT COUNT(*) FROM videos WHERE statut='prete'").fetchone()[0]
    print(f"\n{reste} vidéo(s) prête(s) après contrôle.")


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["stock"]:
        controler_stock()
    else:
        print(__doc__)
