# Here-docs and Here-strings

> **Purpose**: Embed multi-line content (YAML manifests, SQL, shell config) directly in scripts without temporary files, using `<<'EOF'` and `<<<`
> **MCP Validated**: 2026-04-24

## When to Use

- Embedding multi-line YAML to pipe directly to `kubectl apply -f -`
- Providing multi-line SQL blocks to `psql` inside `kubectl exec`
- Configuring containerd or toml inside Docker `exec` calls
- Passing a short string to a command expecting stdin without a temporary file

## Implementation

```bash
#!/usr/bin/env bash
set -euo pipefail

REGISTRY_IP="${REGISTRY_IP:-172.18.0.2}"

# ── Basic here-doc: pipe multi-line YAML to kubectl ──────────────────────────
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: platform-config
  namespace: infra
data:
  environment: "local"
  cluster: "data-platform"
EOF

# ── Here-doc with variable interpolation (no quotes on delimiter) ─────────────
# Single-quote the delimiter ('EOF') to disable interpolation.
# No quotes = variables ARE expanded.
for NODE in $(kind get nodes --name data-platform); do
  docker exec "$NODE" sh -c "
    mkdir -p /etc/containerd/certs.d/${REGISTRY_IP}:5000
    cat > /etc/containerd/certs.d/${REGISTRY_IP}:5000/hosts.toml << 'TOML'
[host.\"http://${REGISTRY_IP}:5000\"]
  capabilities = [\"pull\", \"resolve\", \"push\"]
  skip_verify = true
TOML
  " 2>/dev/null
done

# ── Multi-statement psql via kubectl exec ────────────────────────────────────
POSTGRES_POD=$(kubectl get pod -n infra -l app=postgres \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

kubectl exec -n infra "$POSTGRES_POD" -- psql -U postgres -d sourcedb <<'SQL'
CREATE TABLE IF NOT EXISTS customers (
  customer_id SERIAL PRIMARY KEY,
  email       VARCHAR(255) NOT NULL UNIQUE,
  name        VARCHAR(255) NOT NULL,
  created_at  TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS orders (
  order_id    SERIAL PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
  status      VARCHAR(50)    NOT NULL DEFAULT 'pending',
  amount      NUMERIC(10,2)  NOT NULL,
  created_at  TIMESTAMP DEFAULT NOW()
);
INSERT INTO customers (email, name)
  SELECT 'customer_' || i || '@example.com', 'Customer ' || i
  FROM generate_series(1, 100) AS i
  ON CONFLICT DO NOTHING;
SQL

# ── Indented here-doc (dash variant strips leading tabs) ─────────────────────
# Use real TAB characters (not spaces) for the indent to be stripped.
kubectl apply -f - <<-'EOF'
	apiVersion: v1
	kind: Namespace
	metadata:
	  name: processing
EOF

# ── Here-string: pass single value to stdin ───────────────────────────────────
# Equivalent to: echo "value" | command
PASSWORD=$(kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}')
base64 -d <<< "$PASSWORD"

# ── Here-string: test JSON without a temp file ───────────────────────────────
CONN_STATE='{"connector":{"state":"RUNNING"}}'
jq -e '.connector.state == "RUNNING"' <<< "$CONN_STATE"
```

## Configuration

| Form | Delimiter quoting | Variable expansion | Use |
|------|------------------|--------------------|-----|
| `<<'EOF'` | Quoted | No — literal | Embed static YAML/SQL |
| `<<EOF` | Unquoted | Yes — vars expanded | Embed templated content |
| `<<-'EOF'` | Quoted | No | Indented static (TAB-stripped) |
| `<<<` | N/A | Yes | Single string to stdin |

## Common Mistakes

### Wrong

```bash
# Variables silently not expanded — single-quoted delimiter
NAMESPACE="processing"
kubectl apply -f - <<'EOF'
  namespace: $NAMESPACE   # stays as literal $NAMESPACE
EOF
```

### Correct

```bash
# Remove quotes from delimiter to enable expansion
NAMESPACE="processing"
kubectl apply -f - <<EOF
  namespace: ${NAMESPACE}
EOF

# OR keep quoted (static) and pass via --from-literal
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
```

## See Also

- [patterns/kubectl-scripting.md](kubectl-scripting.md)
- [patterns/tempfile-cleanup.md](tempfile-cleanup.md)
- [concepts/variable-safety.md](../concepts/variable-safety.md)
