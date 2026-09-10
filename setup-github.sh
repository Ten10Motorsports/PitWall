#!/usr/bin/env bash
# PitWall - push this folder to GitHub, correctly.
# The macOS and Linux twin of setup-github.bat. Same behaviour.
set -u
cd "$(dirname "$0")" || exit 1

echo
echo "  PitWall - upload to GitHub"
echo "  ============================================================"
echo

if ! command -v git >/dev/null 2>&1; then
  echo "  Git is not installed, and this script needs it."
  echo "  macOS:  xcode-select --install"
  echo "  Linux:  sudo apt install git   (or your package manager)"
  echo
  exit 1
fi

[ -f server/main.py ] || { echo "  This is not the PitWall folder (no server/main.py). Move this script into it."; echo; exit 1; }
[ -d .github/workflows ] || echo "  Warning: .github is missing here. Re-extract the zip or the automation will not upload."

REPOURL="${1:-}"
if [ -z "$REPOURL" ]; then
  echo "  Paste the address of your EMPTY GitHub repository."
  echo "  It looks like:  https://github.com/YourOrg/YourRepo.git"
  echo
  printf "   Repository URL: "
  read -r REPOURL
fi
[ -n "$REPOURL" ] || { echo; echo "  No URL given. Nothing changed."; exit 1; }
case "$REPOURL" in *github.com*) ;; *) echo; echo "  That is not a GitHub URL. Nothing changed."; exit 1;; esac

echo
echo "  Uploading everything here to:"
echo "    $REPOURL"
echo
echo "  Anything already in that repository will be REPLACED."
printf "   Continue? (y/N): "
read -r GO
case "$GO" in y|Y) ;; *) echo "  Cancelled."; exit 0;; esac

git config user.email >/dev/null 2>&1 || {
  git config --global user.email "you@example.com"
  git config --global user.name "PitWall Setup"
  echo "  [i] Set a placeholder Git name and email."
}

echo
[ -d .git ] && echo "  [1/5] Folder is already prepared." || { echo "  [1/5] Preparing the folder..."; git init -q; }
echo "  [2/5] Collecting every file, including hidden ones..."; git add -A || exit 1
echo "  [3/5] Saving a snapshot..."; git commit -q -m "PitWall" 2>/dev/null || echo "        (nothing new to save - continuing)"
echo "  [4/5] Pointing at your repository..."
git remote remove origin >/dev/null 2>&1 || true
git remote add origin "$REPOURL" || exit 1
git branch -M main
echo "  [5/5] Uploading..."
if ! git push -u origin main --force; then
  echo
  echo "  The upload was refused - check the URL, that the repository exists,"
  echo "  and that you completed the GitHub sign-in."
  exit 1
fi

cat <<'DONE'

  ============================================================
  Done. Everything is on GitHub with its folders intact.

  Next, on the GitHub website:

    1. Settings > Actions > General > Workflow permissions
       Choose "Read and write permissions" and Save.

    2. Settings > Pages > Source
       Choose "GitHub Actions". Ignore the Configure buttons.

    3. Actions tab > "Publish site"      > Run workflow
       Actions tab > "Build Windows app" > Run workflow
  ============================================================

DONE
