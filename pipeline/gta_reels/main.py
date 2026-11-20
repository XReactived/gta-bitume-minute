"""Chef d'orchestre : fait tourner les trois agents 24h/24.

- toutes les N minutes : Agent Clips puis Agent Montage
- à chaque horaire de config.yaml : Agent TikTok publie la meilleure vidéo prête
"""
import datetime as dt
import time
from zoneinfo import ZoneInfo

from . import agent_clips, agent_montage, agent_tiktok, nettoyage
from .commun import config, db, log

L = log("main")


def creneau_du_moment(cfg: dict) -> str | None:
    maintenant = dt.datetime.now(ZoneInfo(cfg["fuseau_horaire"]))
    for h in cfg["tiktok"]["horaires"]:
        hh, mm = map(int, h.split(":"))
        cible = maintenant.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cible <= maintenant < cible + dt.timedelta(minutes=30):
            return f"{maintenant.date()} {h}"
    return None


def boucle() -> None:
    L.info("Pipeline GTA Reels démarré")
    derniere_collecte = 0.0
    while True:
        cfg = config()  # relu à chaque tour : tu peux modifier config.yaml sans redémarrer
        try:
            if time.time() - derniere_collecte >= cfg["clips"]["intervalle_minutes"] * 60:
                derniere_collecte = time.time()
                n = agent_clips.lancer()
                m = agent_montage.lancer()
                L.info("Passage terminé : %d clips téléchargés, %d vidéos montées", n, m)
                nettoyage.lancer()

            cle = creneau_du_moment(cfg)
            if cle:
                conn = db()
                if not conn.execute("SELECT 1 FROM creneaux WHERE cle=?", (cle,)).fetchone():
                    conn.execute("INSERT INTO creneaux (cle) VALUES (?)", (cle,))
                    conn.commit()
                    agent_tiktok.lancer()
        except Exception as e:
            L.exception("Erreur dans la boucle (on continue) : %s", e)
        time.sleep(60)


if __name__ == "__main__":
    boucle()
