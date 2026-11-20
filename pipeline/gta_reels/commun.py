"""Config, base de données et journal partagés par les trois agents."""
import logging
from logging.handlers import RotatingFileHandler
import os
import sqlite3
from pathlib import Path

import yaml
from dotenv import load_dotenv

RACINE = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("GTA_REELS_DATA", RACINE / "data"))
DATA.mkdir(parents=True, exist_ok=True)
(DATA / "brut").mkdir(exist_ok=True)
(DATA / "final").mkdir(exist_ok=True)
(DATA / "segments").mkdir(exist_ok=True)

load_dotenv(RACINE / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(), RotatingFileHandler(DATA / "journal.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")],
)


def log(nom: str) -> logging.Logger:
    return logging.getLogger(nom)


def config() -> dict:
    with open(RACINE / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def env(nom: str) -> str:
    val = os.environ.get(nom, "").strip()
    if not val:
        raise RuntimeError(f"Variable {nom} manquante dans le fichier .env")
    return val


SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    streamer TEXT NOT NULL,
    streamer_login TEXT NOT NULL,
    titre TEXT,
    vues INTEGER,
    cree_le TEXT,
    duree REAL,
    score REAL,
    statut TEXT NOT NULL DEFAULT 'nouveau',  -- nouveau, telecharge, segment, compile, erreur
    fichier_brut TEXT,
    fichier_segment TEXT,
    duree_segment REAL,
    transcription TEXT,
    erreur TEXT,
    ajoute_le TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fichier TEXT NOT NULL,
    clips TEXT NOT NULL,          -- ids des clips utilisés, séparés par des virgules
    score REAL,
    hook TEXT,
    legende TEXT,
    statut TEXT NOT NULL DEFAULT 'prete',  -- prete, publiee, erreur
    publish_id TEXT,
    publiee_le TEXT,
    erreur TEXT,
    creee_le TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS creneaux (cle TEXT PRIMARY KEY);
"""


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DATA / "gta_reels.db", timeout=60)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
