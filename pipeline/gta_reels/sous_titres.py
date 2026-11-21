"""Détecte si un streamer a déjà ses propres sous-titres incrustés dans son stream.

Claude regarde 4 images du clip (une seule fois par streamer, revérifié chaque semaine).
Si oui, on n'ajoute pas nos sous-titres par-dessus (sinon on en voit deux).

Retirer du stock les vidéos déjà montées avec des sous-titres en double :
    python -m gta_reels.sous_titres retirer
"""
import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import anthropic

from .commun import config, db, log, secours

L = log("sous-titres")


def _table(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS streamers_st (login TEXT PRIMARY KEY, sous_titres INTEGER, "
                 "verifie_le TEXT DEFAULT CURRENT_TIMESTAMP)")


def _mosaique(video: str, duree: float) -> bytes:
    """4 images du clip (à 20, 40, 60 et 80 %) réunies en une seule image 2x2."""
    with tempfile.TemporaryDirectory() as d:
        for i, p in enumerate((0.2, 0.4, 0.6, 0.8)):
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{duree * p:.2f}", "-i", video,
                            "-frames:v", "1", "-vf", "scale=960:-2", f"{d}/{i}.jpg"], check=True, timeout=60)
        sortie = Path(d) / "m.jpg"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", f"{d}/%d.jpg", "-vf", "tile=2x2",
                        "-frames:v", "1", "-q:v", "4", str(sortie)], check=True, timeout=60)
        return sortie.read_bytes()


def _demander_a_claude(image: bytes) -> bool:
    rep = anthropic.Anthropic().beta.messages.create(
        model=config()["tiktok"].get("modele_controle") or config()["tiktok"].get("modele_ia", "claude-opus-5-5"),
        max_tokens=600, **secours(config()["tiktok"].get("modele_controle") or config()["tiktok"].get("modele_ia", "")),
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": {
            "type": "object", "properties": {"sous_titres": {"type": "boolean"}, "raison": {"type": "string"}},
            "required": ["sous_titres", "raison"], "additionalProperties": False}}},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(image).decode()}},
            {"type": "text", "text": (
                "Voici 4 images d'un même clip de stream Twitch (GTA). Le streamer affiche-t-il des sous-titres "
                "de sa propre parole incrustés dans l'image (texte qui transcrit ce qu'il dit, souvent en bas ou "
                "au milieu, qui change d'une image à l'autre, parfois en petit avec un mot surligné) ? Ne compte PAS l'interface du jeu, le chat, "
                "les alertes, les noms de joueurs ni les sous-titres des dialogues du jeu.")},
        ]}],
    )
    return bool(json.loads(next(b.text for b in rep.content if b.type == "text"))["sous_titres"])


def traduire(phrases: list[str], streamer: str) -> list[str] | None:
    """Traduit en français les phrases d'un streamer anglophone (une traduction par phrase, même ordre).
    Renvoie None si la traduction échoue : on ne met alors pas de sous-titres."""
    if not phrases:
        return []
    modele = config()["tiktok"].get("modele_controle") or config()["tiktok"].get("modele_ia", "claude-opus-5-5")
    try:
        rep = anthropic.Anthropic().beta.messages.create(
            model=modele, max_tokens=4000, **secours(modele),
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": {
                "type": "object", "properties": {"traductions": {"type": "array", "items": {"type": "string"}}},
                "required": ["traductions"], "additionalProperties": False}}},
            messages=[{"role": "user", "content": (
                f"Voici ce que dit le streamer américain {streamer} pendant un clip GTA 6, découpé en "
                f"{len(phrases)} morceaux. Traduis chaque morceau en français parlé, naturel et court, comme des "
                "sous-titres TikTok (langage familier d'un jeune, garde l'humour et l'énergie, adoucis les gros "
                "mots). Garde les noms propres. Une traduction par morceau, dans le même ordre, exactement "
                f"{len(phrases)} éléments.\n\n" + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(phrases)))}],
        )
        trad = json.loads(next(b.text for b in rep.content if b.type == "text"))["traductions"]
        if len(trad) != len(phrases):
            raise ValueError(f"{len(trad)} traductions pour {len(phrases)} phrases")
        return [t.strip() for t in trad]
    except Exception as e:
        L.warning("Traduction impossible (%s) : clip sans sous-titres", e)
        return None


def a_deja_des_sous_titres(video: str, login: str, duree: float) -> bool:
    login = login.lower()
    if login in {x.lower() for x in config()["montage"].get("streamers_sans_sous_titres") or []}:
        return True
    conn = db()
    _table(conn)
    r = conn.execute("SELECT sous_titres FROM streamers_st WHERE login=? AND verifie_le > datetime('now','-7 days')",
                     (login,)).fetchone()
    if r:
        return bool(r["sous_titres"])
    try:
        oui = _demander_a_claude(_mosaique(video, max(duree, 1.0)))
    except Exception as e:
        L.warning("Détection des sous-titres impossible pour %s (%s) : on ajoute les nôtres", login, e)
        return False
    conn.execute("INSERT OR REPLACE INTO streamers_st (login, sous_titres, verifie_le) VALUES (?,?,CURRENT_TIMESTAMP)",
                 (login, int(oui)))
    conn.commit()
    L.info("%s %s ses propres sous-titres", login, "a déjà" if oui else "n'a pas")
    return oui


def retirer() -> int:
    """Retire du stock les vidéos prêtes qui contiennent un streamer ayant déjà ses sous-titres."""
    conn = db()
    _table(conn)
    logins = {x.lower() for x in config()["montage"].get("streamers_sans_sous_titres") or []}
    logins |= {r["login"] for r in conn.execute("SELECT login FROM streamers_st WHERE sous_titres=1")}
    n = 0
    for v in conn.execute("SELECT id, clips, serie FROM videos WHERE statut='prete'").fetchall():
        ids = v["clips"].split(",")
        qui = {r["streamer_login"].lower() for r in conn.execute(
            f"SELECT streamer_login FROM clips WHERE id IN ({','.join('?' * len(ids))})", ids)}
        if qui & logins:
            if v["serie"]:
                conn.execute("UPDATE videos SET statut='expiree', erreur='sous-titres en double' WHERE serie=?", (v["serie"],))
            else:
                conn.execute("UPDATE videos SET statut='expiree', erreur='sous-titres en double' WHERE id=?", (v["id"],))
            n += 1
    conn.commit()
    reste = conn.execute("SELECT COUNT(*) FROM videos WHERE statut='prete'").fetchone()[0]
    print(f"{n} vidéo(s) retirée(s) du stock (streamers : {', '.join(sorted(logins))}). Il en reste {reste} prête(s).")
    return n


if __name__ == "__main__":
    if sys.argv[1:] == ["retirer"]:
        retirer()
    else:
        print(__doc__)
