"""Fabrique tout de suite une vidéo d'essai avec le montage actuel, pour la regarder avant publication.

    python -m gta_reels.essai

Reprend les meilleurs clips récents (re-téléchargés depuis Twitch), les remonte et les assemble en une vidéo.
La vidéo d'essai n'est jamais publiée (statut « essai ») : les clips ont déjà servi.
"""
from .agent_clips import telecharger
from .agent_montage import fabriquer_partie, preparer_segment
from .commun import DATA, config, db, log

L = log("essai")


def lancer(nb: int = 3) -> None:
    conn = db()
    choisis = conn.execute(
        "SELECT * FROM clips WHERE statut IN ('segment','compile') AND score >= 7 "
        "ORDER BY ajoute_le DESC LIMIT ?", (nb,)).fetchall()
    if not choisis:
        print("Aucun clip récent noté 7/10 ou plus : réessaie plus tard.")
        return
    prets, origine = [], {c["id"]: c["statut"] for c in choisis}
    for c in choisis:
        statut = origine[c["id"]]
        try:
            brut = telecharger(c["url"], c["id"])
            conn.execute("UPDATE clips SET fichier_brut=? WHERE id=?", (brut, c["id"]))
            conn.commit()
            preparer_segment(conn.execute("SELECT * FROM clips WHERE id=?", (c["id"],)).fetchone())
            r = conn.execute("SELECT * FROM clips WHERE id=?", (c["id"],)).fetchone()
            if r["statut"] == "segment":
                prets.append(r)
            else:
                print(f"Clip de {c['streamer']} écarté : {r['erreur']}")
        finally:  # le clip garde son état d'origine : il ne sera pas republié
            conn.execute("UPDATE clips SET statut=? WHERE id=?", (statut, c["id"]))
            conn.commit()
    if not prets:
        print("Aucun clip n'a passé le contrôle.")
        return
    fmt = "top" if len(prets) > 1 else "solo"
    prets.sort(key=lambda r: r["score"] or 0)
    fabriquer_partie(prets, fmt, 1, 1, None, float(config()["montage"].get("duree_max_s", 90)))
    for i, statut in origine.items():  # les clips pas encore utilisés restent disponibles pour de vraies vidéos
        conn.execute("UPDATE clips SET statut=? WHERE id=?", (statut, i))
    v = conn.execute("SELECT * FROM videos ORDER BY id DESC LIMIT 1").fetchone()
    conn.execute("UPDATE videos SET statut='essai' WHERE id=?", (v["id"],))
    conn.commit()
    chemin = str(v["fichier"]).replace(str(DATA), "/opt/bitume-minute/data")
    print(f"Vidéo d'essai : {fmt}, {v['duree']:.0f} s, contrôle qualité : "
          f"{'OK' if v['statut'] == 'prete' else 'refusée — ' + (v['erreur'] or '')}")
    print(f"Fichier sur le serveur : {chemin}")


if __name__ == "__main__":
    lancer()
