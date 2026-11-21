"""Voix-off française gratuite, calculée sur le serveur (Piper, sans abonnement)."""
import os
import re
import wave
from pathlib import Path

from .commun import config, log

L = log("voix")
DOSSIER_VOIX = Path(os.environ.get("GTA_REELS_VOIX", "/app/voix"))
_voix = None


def _charger():
    global _voix
    if _voix is None:
        from piper import PiperVoice

        nom = config()["montage"].get("voix_modele", "fr_FR-siwis-medium")
        modele = DOSSIER_VOIX / f"{nom}.onnx"
        if not modele.exists():
            from piper.download_voices import download_voice

            DOSSIER_VOIX.mkdir(parents=True, exist_ok=True)
            download_voice(nom, DOSSIER_VOIX)
        _voix = PiperVoice.load(modele)
    return _voix


# Mots que la voix lit mal : on les écrit comme ils se prononcent (complété par montage.prononciation dans config.yaml)
PRONONCIATION = {
    "LeoTheCrack": "Léo ze crack", "Leo The Crack": "Léo ze crack", "Leo": "Léo",
    "Squeezie": "Squizi", "Anyme023": "Anime", "Anyme": "Anime", "ZeratoR": "Zérator",
    "JLTomy": "Ji-elle Tomi", "Inoxtag": "Inox tag", "Gotaga": "Gotaga", "Michou": "Michou",
    "Rivenzi": "Rivenzi", "Domingo": "Domingo", "Ponce": "Ponce", "Twitch": "Touitch",
    "GTA VI": "G T A six", "GTA 6": "G T A six", "GTA": "G T A", "TikTok": "Tik Tok", "stream": "strim",
    "streamer": "strimeur", "streameur": "strimeur", "streamers": "strimeurs", "live": "laïve",
}


def prononcer(texte: str) -> str:
    table = {**PRONONCIATION, **(config()["montage"].get("prononciation") or {})}
    for mot in sorted(table, key=len, reverse=True):  # les plus longs d'abord (LeoTheCrack avant Leo)
        texte = re.sub(rf"(?<![\wÀ-ÿ]){re.escape(mot)}(?![\wÀ-ÿ])", table[mot], texte, flags=re.IGNORECASE)
    return texte


CONSIGNE_VOIX = ("Style coach sportif survolté : voix masculine française de France, très énergique, motivante "
                 "et punchy, qui donne envie de rester jusqu'au bout. Phrases lancées avec élan, accents forts sur "
                 "les mots clés, enthousiasme sincère, rythme rapide. Prononce les pseudos de streamers à la "
                 "française : Leo se dit Léo, LeoTheCrack se dit Léo ze crack, Squeezie se dit Squizi, Anyme023 se dit "
                 "Anime, Twitch se dit Touitch.")


def _dire_openai(texte: str, sortie: Path) -> float:
    """Voix réaliste (OpenAI, environ 1 à 2 € par mois pour 5 vidéos par jour)."""
    import requests

    m = config()["montage"]
    perso = m.get("prononciation") or {}
    for mot in sorted(perso, key=len, reverse=True):
        texte = re.sub(rf"(?<![\wÀ-ÿ]){re.escape(mot)}(?![\wÀ-ÿ])", perso[mot], texte, flags=re.IGNORECASE)
    r = requests.post("https://api.openai.com/v1/audio/speech", timeout=60,
                      headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY'].strip()}"},
                      json={"model": m.get("voix_openai_modele", "gpt-4o-mini-tts"),
                            "voice": m.get("voix_openai", "ash"), "input": texte,
                            "instructions": m.get("voix_consigne") or CONSIGNE_VOIX,
                            "response_format": "pcm"})  # 24 kHz, 16 bits, mono
    r.raise_for_status()
    with wave.open(str(sortie), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(r.content)
    return len(r.content) / 2 / 24000


def dire(texte: str, sortie: Path) -> float | None:
    """Écrit la phrase en .wav et renvoie sa durée en secondes (None si la voix est indisponible)."""
    if config()["montage"].get("voix_moteur", "piper") == "openai" and os.environ.get("OPENAI_API_KEY", "").strip():
        try:
            return _dire_openai(texte, sortie)
        except Exception as e:
            L.warning("Voix réaliste indisponible (%s) : voix gratuite utilisée", e)
    try:
        from piper import SynthesisConfig

        with wave.open(str(sortie), "wb") as w:
            _charger().synthesize_wav(prononcer(texte), w, syn_config=SynthesisConfig(length_scale=0.92))
        with wave.open(str(sortie), "rb") as r:
            return r.getnframes() / r.getframerate()
    except Exception as e:
        L.warning("Voix-off indisponible (%s) : carte sans voix", e)
        return None
