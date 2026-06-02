# AWS EC2 — quick command reference

Operational commands for the Ariadne single-EC2 deploy. Run all `aws ec2 ...`
commands from your laptop (not from inside the EC2).

**Instance ID:** `i-05c75dd163ae68142`
**Public IP:** `34.227.165.65` (Elastic IP — stable across stop/start)

> If you ever lose track of these, run `terraform output` from `deploy/aws/`.

---

## Stop the instance (save money)

```bash
aws ec2 stop-instances --instance-ids i-05c75dd163ae68142
```

- Bill drops from ~$1/day to ~$0.08/day (EBS storage only)
- Data preserved: Neo4j graph, Postgres rows, parsed files all survive
- Containers auto-resume on next start

Wait for it to fully stop:
```bash
aws ec2 wait instance-stopped --instance-ids i-05c75dd163ae68142
```

---

## Start the instance

```bash
aws ec2 start-instances --instance-ids i-05c75dd163ae68142
```

Wait until it's fully running and the stack is healthy:
```bash
aws ec2 wait instance-running --instance-ids i-05c75dd163ae68142

# Then poll the gateway health endpoint until it answers (~30-60 seconds
# after instance-running because Docker takes time to bring containers up):
until curl -fs http://34.227.165.65:8000/health 2>/dev/null; do
  sleep 5
  echo "waiting for stack..."
done
echo ""
echo "Stack is up at http://34.227.165.65:3000"
```

EIP is the same, so all your URLs still work.

---

## Check current state

```bash
aws ec2 describe-instances \
  --instance-ids i-05c75dd163ae68142 \
  --query 'Reservations[0].Instances[0].State.Name' \
  --output text
```

Prints one of: `running`, `stopped`, `pending`, `stopping`, `terminated`.

---

## SSH in

```bash
ssh -i ~/.ssh/ariadne-demo.pem ec2-user@34.227.165.65
```

---

## Inspect the running stack (from inside the EC2)

```bash
# All Ariadne containers + health
sudo docker ps --filter "name=lineage-" --format "table {{.Names}}\t{{.Status}}"

# Tail logs of any service
sudo docker logs --tail 50 lineage-gateway
sudo docker logs --tail 50 lineage-qlikview-parser
sudo docker logs --tail 50 lineage-tws-parser

# Follow logs live (Ctrl+C to stop)
sudo docker logs -f lineage-gateway
```

---

## Restart the stack without rebooting the EC2

```bash
# From inside the EC2
cd ~/ariadne/lineage-platform
sudo docker compose restart                 # restart all containers
sudo docker compose restart gateway         # restart just one
```

---

## Pull new code and rebuild

```bash
# From inside the EC2
cd ~/ariadne
git pull
cd lineage-platform
sudo docker compose up -d --build           # rebuild whatever changed
```

To force-rebuild from scratch (no cache):
```bash
sudo docker compose build --no-cache <service-name>
sudo docker compose up -d <service-name>
```

---

## Permanent teardown

Wipes the EC2, EBS volume (and all parsed data), Elastic IP, and security
group. Your SSH key pair stays in AWS.

```bash
cd ~/Desktop/Multiparser-Knoweledge-Graph/deploy/aws
terraform destroy
```

Takes ~90 seconds. Confirm with `yes` when prompted.
