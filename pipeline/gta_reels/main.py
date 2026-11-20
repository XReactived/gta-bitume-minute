"""Chef d'orchestre : fait tourner les trois agents 24h/24.

- toutes les N minutes : Agent Clips puis Agent Montage
- en parallèle, à chaque horaire de config.yaml : Agent TikTok publie la meilleure vidéo prête
"""
import datetime as dt
import threading
import time
from zoneinfo import ZoneInfo

from . import agent_clips, agent_montage, agent_tiktok, nettoyage
from .commun import config, db, log

L = log("main")


def horaires_du_jour(cfg: dict, jour: dt.datetime) -> list[str]:
    """Les horaires peuvent être une simple liste, ou différer selon le jour (semaine, mercredi, week-end)."""
    h = cfg["tiktok"]["horaires"]
    if isinstance(h, list):
        return h
    if jour.weekday() >= 5:
        return h.get("weekend") or h["semaine"]
    if jour.weekday() == 2:
        return h.get("mercredi") or h["semaine"]
    return h["semaine"]


def creneau_du_moment(cfg: dict) -> str | None:
    maintenant = dt.datetime.now(ZoneInfo(cfg["fuseau_horaire"]))
    for h in horaires_du_jour(cfg, maintenant):
        hh, mm = map(int, h.split(":"))
        cible = maintenant.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cible <= maintenant < cible + dt.timedelta(minutes=30):
            return f"{maintenant.date()} {h}"
    return None


def boucle_publication() -> None:
    """Fil séparé : publie aux horaires, même si un long montage est en cours."""
    while True:
        try:
            cfg = config()
            cle = creneau_du_moment(cfg)
            if cle:
                conn = db()
                if not conn.execute("SELECT 1 FROM creneaux WHERE cle=?", (cle,)).fetchone():
                    L.info("Créneau %s : envoi d'une vidéo vers TikTok", cle)
                    try:
                        agent_tiktok.lancer()
                    finally:
                        # noté seulement une fois l'envoi terminé : si le serveur redémarre pendant l'envoi,
                        # le créneau est retenté au redémarrage au lieu d'être perdu
                        conn.execute("INSERT OR IGNORE INTO creneaux (cle) VALUES (?)", (cle,))
                        conn.commit()
        except Exception as e:
            L.exception("Erreur de publication (on continue) : %s", e)
        time.sleep(30)


def boucle() -> None:
    L.info("Pipeline GTA Reels démarré")
    threading.Thread(target=boucle_publication, daemon=True, name="publication").start()
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
        except Exception as e:
            L.exception("Erreur dans la boucle (on continue) : %s", e)
        time.sleep(60)


if __name__ == "__main__":
    boucle()
