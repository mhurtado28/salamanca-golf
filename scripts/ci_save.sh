#!/usr/bin/env bash
# Guarda en la rama los datos/figuras generados (para GitHub Actions).
# Uso: bash scripts/ci_save.sh "mensaje del commit"
set -u
cd "$(git rev-parse --show-toplevel)"
git config user.name "github-actions[bot]"
git config user.email "41898283+github-actions[bot]@users.noreply.github.com"
git add data figures 2>/dev/null || true
if git diff --cached --quiet; then
  echo "Sin cambios que guardar."
  exit 0
fi
git commit -q -m "$1"
for i in 1 2 3; do
  git pull -q --rebase origin "${GITHUB_REF_NAME}" && git push -q origin "HEAD:${GITHUB_REF_NAME}" && echo "Guardado: $1" && exit 0
  sleep 10
done
echo "No se pudo subir el commit." ; exit 1
