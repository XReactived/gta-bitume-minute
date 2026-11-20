#!/usr/bin/env bash
# Installation de GTA Bitume Minute sur un serveur Ubuntu neuf.
# Usage :  curl -fsSL https://raw.githubusercontent.com/XReactived/gta-bitume-minute/main/install.sh | bash
set -euo pipefail

DOSSIER=/opt/bitume-minute
REDIRECT=https://xreactived.github.io/gta-bitume-minute/callback.html
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"

echo
echo "=== GTA Bitume Minute : installation ==="
echo

# 1. Docker
if ! command -v docker >/dev/null 2>&1; then
  echo "[1/5] Installation de Docker (2 à 3 minutes)..."
  curl -fsSL https://get.docker.com | $SUDO sh >/dev/null
else
  echo "[1/5] Docker déjà installé."
fi

# 2. Code
echo "[2/5] Téléchargement du pipeline..."
TMP=$(mktemp -d)
curl -fsSL https://github.com/XReactived/gta-bitume-minute/archive/refs/heads/main.tar.gz | tar xz -C "$TMP"
$SUDO mkdir -p "$DOSSIER"
# met à jour le code sans toucher aux clés (.env), aux réglages (config.yaml) ni aux données
$SUDO cp -r "$TMP"/gta-bitume-minute-main/pipeline/gta_reels "$TMP"/gta-bitume-minute-main/pipeline/Dockerfile \
  "$TMP"/gta-bitume-minute-main/pipeline/docker-compose.yml "$TMP"/gta-bitume-minute-main/pipeline/requirements.txt \
  "$TMP"/gta-bitume-minute-main/pipeline/README.md "$DOSSIER"/
[ -f "$DOSSIER/config.yaml" ] || $SUDO cp "$TMP"/gta-bitume-minute-main/pipeline/config.yaml "$DOSSIER"/
rm -rf "$TMP"
$SUDO mkdir -p "$DOSSIER/data"

# 3. Clés
if [ ! -f "$DOSSIER/.env" ]; then
  echo "[3/5] Colle tes clés (rien ne s'affiche quand tu colles, c'est normal), puis appuie sur Entrée."
  demander() { local v; read -r -s -p "  $1 : " v </dev/tty; echo >&2; printf '%s' "$v"; }
  TWITCH_ID=$(demander "Twitch Client ID")
  TWITCH_SECRET=$(demander "Twitch Client Secret")
  ANTHROPIC=$(demander "Clé Anthropic (commence par sk-ant-)")
  TT_KEY=$(demander "TikTok Client key (Sandbox)")
  TT_SECRET=$(demander "TikTok Client secret (Sandbox)")
  $SUDO tee "$DOSSIER/.env" >/dev/null <<EOF
TWITCH_CLIENT_ID=$TWITCH_ID
TWITCH_CLIENT_SECRET=$TWITCH_SECRET
ANTHROPIC_API_KEY=$ANTHROPIC
TIKTOK_CLIENT_KEY=$TT_KEY
TIKTOK_CLIENT_SECRET=$TT_SECRET
TIKTOK_REDIRECT_URI=$REDIRECT
EOF
  $SUDO chmod 600 "$DOSSIER/.env"
else
  echo "[3/5] Clés déjà enregistrées."
fi

# 4. Construction
echo "[4/5] Préparation des agents (5 à 10 minutes la première fois)..."
cd "$DOSSIER"
$SUDO docker compose build -q

# 5. TikTok puis démarrage
if [ ! -f "$DOSSIER/data/tiktok_token.json" ]; then
  echo "[5/5] Connexion du compte TikTok :"
  $SUDO docker compose run --rm gta-reels python -m gta_reels.tiktok_auth </dev/tty
else
  echo "[5/5] Compte TikTok déjà relié."
fi
$SUDO docker compose up -d

echo
echo "=== C'est en marche ! ==="
echo "Voir ce que font les agents :  cd $DOSSIER && sudo docker compose logs -f"
echo "(Ctrl+C pour quitter l'affichage, les agents continuent de tourner.)"
