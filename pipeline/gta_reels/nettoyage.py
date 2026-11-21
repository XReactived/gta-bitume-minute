"""Fait le ménage sur le disque pour que les 40 Go du serveur suffisent toujours."""
import shutil
from pathlib import Path

from .commun import DATA, db, log

L = log("nettoyage")


def _suppr(chemin: str | Path | None) -> None:
    if not chemin:
        return
    p = Path(chemin)
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    elif p.exists():
        p.unlink(missing_ok=True)


def lancer() -> None:
    conn = db()
    # Clips trop vieux jamais utilisés : plus d'actualité, on les abandonne
    conn.execute("UPDATE clips SET statut='expire' WHERE statut IN ('telecharge','segment') "
                 "AND ajoute_le < datetime('now','-3 days')")
    # Vidéos prêtes mais jamais publiées depuis 7 jours : périmées
    conn.execute("UPDATE videos SET statut='expiree' WHERE statut='prete' AND creee_le < datetime('now','-7 days')")
    # Partie d'une série dont la partie précédente a échoué ou expiré : on ne la publiera jamais
    conn.execute("UPDATE videos SET statut='expiree' WHERE statut='prete' AND serie IS NOT NULL AND partie > 1 "
                 "AND EXISTS (SELECT 1 FROM videos p WHERE p.serie=videos.serie AND p.partie=videos.partie-1 "
                 "AND p.statut IN ('erreur','expiree','rejetee'))")
    conn.commit()

    n = 0
    for c in conn.execute("SELECT id, fichier_brut, statut, ajoute_le < datetime('now','-3 days') AS vieux FROM clips WHERE statut IN ('segment','compile','erreur','expire','refuse')"):
        if c["fichier_brut"] and Path(c["fichier_brut"]).exists():
            _suppr(c["fichier_brut"]); n += 1  # le brut ne sert plus une fois le segment fait
        if c["statut"] in ("erreur", "expire", "refuse") or (c["statut"] == "compile" and c["vieux"]):
            d = DATA / "segments" / c["id"]
            if d.exists():
                _suppr(d); n += 1
    for v in conn.execute("SELECT fichier FROM videos WHERE (statut IN ('publiee','erreur') "
                          "AND creee_le < datetime('now','-3 days')) OR statut IN ('expiree','rejetee') "
                          "OR (statut='essai' AND creee_le < datetime('now','-2 days'))"):
        d = Path(v["fichier"]).parent
        if d.exists():
            _suppr(d); n += 1
    if n:
        L.info("Ménage : %d fichiers ou dossiers supprimés", n)
