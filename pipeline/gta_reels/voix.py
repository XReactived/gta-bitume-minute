"""Voix-off française gratuite, calculée sur le serveur (Piper, sans abonnement)."""
import os
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


def dire(texte: str, sortie: Path) -> float | None:
    """Écrit la phrase en .wav et renvoie sa durée en secondes (None si la voix est indisponible)."""
    try:
        from piper import SynthesisConfig

        with wave.open(str(sortie), "wb") as w:
            _charger().synthesize_wav(texte, w, syn_config=SynthesisConfig(length_scale=0.92))
        with wave.open(str(sortie), "rb") as r:
            return r.getnframes() / r.getframerate()
    except Exception as e:
        L.warning("Voix-off indisponible (%s) : carte sans voix", e)
        return None
