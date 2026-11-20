"""Notifications sur le téléphone via l'appli gratuite ntfy (https://ntfy.sh), sans compte.

Le nom du « sujet » est créé au hasard au premier lancement et gardé dans data/ntfy_sujet.txt.
Pour le connaître et recevoir une notification de test :  python -m gta_reels.notification
"""
import secrets

import requests

from .commun import DATA, config, log

L = log("notification")
FICHIER = DATA / "ntfy_sujet.txt"


def sujet() -> str:
    if not FICHIER.exists():
        FICHIER.write_text("bitume-" + secrets.token_hex(8), encoding="utf-8")
    return FICHIER.read_text(encoding="utf-8").strip()


def notifier(titre: str, message: str, priorite: int = 4, tags: list[str] | None = None) -> None:
    n = config().get("notifications", {})
    if not n.get("actif", True):
        return
    try:
        r = requests.post(n.get("serveur", "https://ntfy.sh"), timeout=15, json={
            "topic": sujet(), "title": titre, "message": message[:3500], "priority": priorite,
            "tags": tags or [], "click": "https://www.tiktok.com/"})
        r.raise_for_status()
    except Exception as e:  # une notification ratée ne doit jamais bloquer la publication
        L.warning("Notification non envoyée : %s", e)


if __name__ == "__main__":
    s = sujet()
    notifier("Bitume Minute est relié à ton téléphone", "Tu recevras ici chaque vidéo à publier, avec sa légende.",
             3, ["white_check_mark"])
    print(f"\nDans l'appli ntfy, appuie sur + et abonne-toi au sujet :\n\n    {s}\n")
