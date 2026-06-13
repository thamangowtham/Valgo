# Runbook

Daily operations and incident response for Valgo.

## Daily checklist

### Pre-market (08:30 IST onwards)

- [ ] **08:45 IST — auth refresh fires automatically.** Check the SNS alert email or CloudWatch logs:
  ```
  /aws/lambda/valgo-prod-auth-refresh
  ```
  Look for `auth_refresh.token_written`. If it failed, jump to *Incident: auth refresh failed* below.

- [ ] **08:50 IST — verify ingestor reconnected** with the new token:
  ```bash
  aws logs tail /ecs/valgo-prod-ingestor --since 10m | grep -E "kite\.connected|kite\.error"
  ```

- [ ] **08:55 IST — check execution nodes are running:**
  ```bash
  aws ec2 describe-instances --filters "Name=tag:Name,Values=valgo-exec-node-*" \
    --query 'Reservations[].Instances[].[InstanceId,State.Name,PrivateIpAddress]' --output table
  ```

- [ ] **09:00 IST — admin panel sanity check.** Open the panel, confirm:
  - Top bar: "feed: kite · FULL ●" (green dot)
  - Top bar: "market: open"
  - Strategies count matches expected
  - Nodes count matches expected (no kill-switch banner)

### During market hours (09:15 — 15:30 IST)

- Monitor the dashboard view. Watch for:
  - Reject rate climbing (expect <2% steady-state)
  - Tick latency above 50ms in the system health panel
  - Orders/sec approaching cap (10/sec)

### Post-market (15:30 IST onwards)

- [ ] Review audit log for the day's orders. Filter to REJECTED and investigate any unexpected rejections.
- [ ] Check daily P&L ledger in DDB:
  ```bash
  aws dynamodb get-item --table-name valgo_prod_audit \
    --key '{"event_id":{"S":"daily_summary_$(date +%F)"}}'
  ```
- [ ] If you deployed code today, tail logs once more for any late errors.

---

## Incidents

### Incident: auth refresh failed

**Symptom:** SNS email at ~08:46 IST saying "Valgo: daily auth refresh FAILED", or no email at all by 08:50.

**First check:** Lambda logs.
```
aws logs tail /aws/lambda/valgo-prod-auth-refresh --since 30m
```

Common causes:
1. **TOTP seed wrong.** Lambda log shows `twofa_value invalid`. Re-fetch your seed from broker, update the secret:
   ```
   aws secretsmanager update-secret --secret-id valgo/kite/totp_seed --secret-string '<new-seed>'
   ```
2. **Password changed.** Update `valgo/kite/password` secret.
3. **Kite login API changed.** Has happened before. Check Kite developer forum, then patch `kite_login.py`.

**Manual recovery:** SSH to your laptop, run:
```bash
source .venv/bin/activate
python -m services.auth_refresh.kite_login
# Paste request_token when prompted
# Take the printed access_token
aws secretsmanager update-secret --secret-id valgo/kite/access_token --secret-string '<token>'
```

Then restart the ingestor and execution nodes so they pick up the fresh token:
```bash
aws ecs update-service --cluster valgo-prod --service ingestor --force-new-deployment
```

### Incident: feed disconnected

**Symptom:** Top bar shows red dot on feed, OR ingestor logs show `kite.gave_up_reconnecting`.

**First check:** Is it our network or theirs?
```bash
# From any service in the VPC:
curl -v wss://ws.kite.trade
# Or check Kite status page
```

**If theirs (and Fyers backup is configured):** failover should have promoted Fyers. Verify in admin panel: feed should show "fyers · FULL ●".

**If ours:** check NAT Gateway:
```bash
aws ec2 describe-nat-gateways --filter "Name=tag:Name,Values=valgo-prod-nat"
```

Verify the EIP is still attached. If not, the broker whitelist is broken — see *Static IP changed* below.

### Incident: orders rejecting at the broker

**Symptom:** audit log shows REJECTED status, rejection_reason from broker.

**Common causes:**

| Reason | What it means | Fix |
|--------|---------------|-----|
| `Margin shortfall` | Account doesn't have enough margin | Top up the broker account |
| `Order frozen quantity` | Single order > exchange freeze qty | Split into smaller orders in the strategy |
| `Trading not allowed for this segment` | Account isn't enabled for F&O | Enable in broker UI |
| `IP not whitelisted` | Whitelisted IP doesn't match NAT EIP | See *Static IP changed* |

### Incident: rate limit hits

**Symptom:** Router returns 429s, logs show `rate_limit.exceeded`.

If steady — your strategy is over-trading. Either:
- Reduce strategy frequency
- Apply for an exchange-approved higher rate (institutional clients only — unlikely for personal)

If burst — check for a runaway strategy. Engage kill switch from admin panel, find which strategy fired the storm via the audit log.

### Incident: kill switch engaged

The kill switch is a Redis flag; engaging it from the admin panel sets `risk:kill_switch=1`. The router checks this on every order.

**To release:** uncheck in admin panel, OR:
```bash
redis-cli -h <redis-endpoint> DEL risk:kill_switch
```

**Engage manually if needed:**
```bash
redis-cli -h <redis-endpoint> SET risk:kill_switch 1
```

### Incident: static IP changed

If the NAT Gateway's EIP ever changes (it shouldn't — it's persistent), every order will reject with "IP not whitelisted".

**Get the current EIP:**
```bash
cd infra/envs/prod
terraform output whitelist_ip
```

**Update with broker.** Zerodha: log into kite developer console, update API app's whitelisted IP.

**Restart execution nodes** to clear any stale connection pools:
```bash
aws ec2 reboot-instances --instance-ids i-xxx i-yyy ...
```

---

## Common debugging

### Tail all logs in dev
```bash
docker compose logs -f
```

### Check Redis hot keys
```bash
docker exec -it valgo-redis redis-cli
> KEYS tick:*
> GET tick:full:NIFTY26500CE
> GET risk:kill_switch
> GET rate:orders:a1
```

### Inspect DynamoDB locally
Open http://localhost:8001 (dynamodb-admin)

### Replay a webhook for testing
```bash
SECRET=$(grep TRADINGVIEW_SHARED_SECRET .env | cut -d= -f2)
BODY='{"strategy_id":"s1","tradingsymbol":"NIFTY26500CE","side":"BUY","quantity":50,"price":142.5,"idempotency_key":"test-$(date +%s)"}'
SIG=$(echo -n "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | cut -d' ' -f2)
curl -X POST http://localhost:8092/webhook/tv/test \
  -H "X-Signature: $SIG" \
  -H "Content-Type: application/json" \
  -d "$BODY"
```

### Manually engage / release kill switch
From admin panel: Risk section → toggle "Engage kill switch".
From CLI: see *Incident: kill switch engaged* above.

---

## Deployment

### Dev
```bash
cd infra/envs/dev
terraform plan
terraform apply
```

### Prod
```bash
cd infra/envs/prod
terraform plan -out=tfplan
terraform apply tfplan
```

### Update Lambda after handler.py changes
```bash
./scripts/package_lambda.sh
cd infra/envs/prod
terraform apply -target=module.valgo.module.auth.aws_lambda_function.auth_refresh
```

### Roll a service
```bash
aws ecs update-service --cluster valgo-prod --service <service-name> --force-new-deployment
```

---

## Things to never do

- **Never release the NAT Gateway EIP.** It's whitelisted with the broker. Releasing it means broker re-registration.
- **Never run the system without the kill switch tested.** Test it weekly: from admin panel, engage → place a test order → verify it's rejected with `kill_switch_engaged` → release.
- **Never store the access_token in code or .env in production.** It changes daily. Always fetch from Secrets Manager.
- **Never disable rate limiting "just for one trade".** It's the only thing protecting you from a runaway strategy.
