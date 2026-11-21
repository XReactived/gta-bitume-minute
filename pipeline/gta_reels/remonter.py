"""Jette le stock monté avec l'ancien montage et remet ses bons clips dans la file, pour qu'ils soient
remontés avec le montage actuel (voix, teaser, zoom…). Les clips déjà publiés ne sont jamais repris.

    python -m gta_reels.remonter
"""
from .agent_clips import telecharger
from .commun import db, log

L = log("remonter")


def lancer(maxi: int = 15) -> None:
    conn = db()
    publies = {i for (c,) in conn.execute("SELECT clips FROM videos WHERE statut='publiee'") for i in (c or "").split(",")}
    n = conn.execute("UPDATE videos SET statut='expiree', erreur='ancien montage' WHERE statut='prete'").rowcount
    conn.commit()
    print(f"{n} vidéo(s) de l'ancien stock retirée(s) (jamais envoyées).")
    clips = [c for c in conn.execute(
        "SELECT * FROM clips WHERE statut IN ('segment','compile') AND ajoute_le >= datetime('now','-4 days') "
        "ORDER BY score DESC").fetchall() if c["id"] not in publies][:maxi]
    ok = 0
    for c in clips:
        try:
            brut = telecharger(c["url"], c["id"])
            conn.execute("UPDATE clips SET statut='telecharge', fichier_brut=?, fichier_segment=NULL, erreur=NULL "
                         "WHERE id=?", (brut, c["id"]))
            conn.commit()
            ok += 1
        except Exception as e:
            L.warning("Clip %s pas re-téléchargé : %s", c["id"], e)
    print(f"{ok} clip(s) remis dans la file : ils seront remontés avec le nouveau montage aux prochains passages.")


if __name__ == "__main__":
    lancer()
