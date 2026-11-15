#!/bin/bash
# Run:  bash ~/Desktop/finn-enam/push_to_github.sh
set -e
cd ~/Desktop/finn-enam
rm -rf .git
git init -q -b main
git add .
# warmed caches so a cold Render restart is fast (force-add past the *.db ignore rule)
git add -f backend/data/push.db backend/data/pull.db backend/data/concalls.db
echo "---- env-like files (should list only .env.example files) ----"
git ls-files | grep -iE '(^|/)\.env' || true
echo "---- key-string scan (should print nothing) ----"
git grep -nE 'AIza[0-9A-Za-z_-]{20,}|AQ\.Ab8' || true
echo "---- warmed DBs staged (should list 3) ----"
git ls-files backend/data | grep -E '\.db$'
read -p "Looks clean? Press Enter to commit and push, Ctrl+C to abort: " _
git commit -q -m "FINN: mobile-first NSE investment research and filing alert agent"
git remote add origin https://github.com/harshbansal5114-svg/finn-enam.git
git push -u origin main --force
echo "Pushed: https://github.com/harshbansal5114-svg/finn-enam"
