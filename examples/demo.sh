#!/usr/bin/env bash
# Builds a throwaway repo, saves two memories, changes and moves code, and shows anchormem catching it.
# Run from anywhere: bash examples/demo.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$HERE/src${PYTHONPATH:+:$PYTHONPATH}"
am() {
  local shown="" a
  for a in "$@"; do [[ "$a" == *" "* ]] && shown+=" \"$a\"" || shown+=" $a"; done
  echo; echo "\$ anchormem$shown"; python3 -m anchormem "$@" || true
}
g() { git -c user.name=demo -c user.email=demo@example.com "$@" >/dev/null; }

DEMO="$(mktemp -d)"
trap 'rm -rf "$DEMO"' EXIT
cd "$DEMO"
g init -q
mkdir -p src/db src/billing
cat > src/db/pool.py <<'EOF'
POOL_SIZE = 10

def get_pool():
    return create_pool(size=POOL_SIZE)
EOF
cat > src/billing/invoice.ts <<'EOF'
export function lineTotal(qty: number, price: number): number {
  return Math.round(qty * price * 100) / 100;
}
EOF
g add -A && g commit -q -m "initial"

am init
am add "Pool capped at 10 because of the Postgres plan limit" \
   --anchor src/db/pool.py#POOL_SIZE \
   --body "Hosted plan allows 25 connections across 2 workers. Raise it only after upgrading."
am add "Invoices round per line item, then total" --anchor src/billing/invoice.ts#lineTotal
g add -A && g commit -q -m "add memories"
am check

echo; echo "--- someone raises the pool size and renames invoice.ts ---"
sed -i.bak 's/POOL_SIZE = 10/POOL_SIZE = 25/' src/db/pool.py && rm src/db/pool.py.bak
g mv src/billing/invoice.ts src/billing/lines.ts
g commit -q -am "bump pool, rename invoice"

am check
am context src/db/pool.py
echo; echo "--- the rounding rule just moved files: follow it. The pool memory is now wrong, so it stays flagged ---"
am verify invoices-round-per-line-item --follow
am check
