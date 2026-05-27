"""Ping all conversations in registry.json and print input_tokens."""
import json, requests
from collections import defaultdict

REGISTRY = "C:/Users/温学周/Desktop/langgraph_test/sandbox/tmp/.agent_runtime/registry.json"
API_KEY = "kaguya"

with open(REGISTRY, encoding="utf-8") as f:
    registry = json.load(f)

all_convs = []
for name, cfg in registry["agents"].items():
    for conv in cfg.get("conversations", []):
        all_convs.append((name, conv, cfg["port"], cfg["profile"]))

print(f"共 {len(all_convs)} 个 conversation:\n")

results = []
for agent, conv, port, profile in all_convs:
    try:
        resp = requests.post(
            f"http://127.0.0.1:{port}/v1/responses",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={"input": "ping", "conversation": conv},
            timeout=10,
        )
        if resp.status_code == 200:
            u = resp.json().get("usage", {})
            inp = u.get("input_tokens", 0)
            out = u.get("output_tokens", 0)
            inp_s = f"{inp:,}"
            results.append((agent, conv, port, inp, out))
        else:
            inp_s = "ERR"
            results.append((agent, conv, port, -1, -1))
    except Exception as e:
        inp_s = "FAIL"
        results.append((agent, conv, port, -2, -2))

    print(f"  [{agent:<10}] input={inp_s:>10}  ({conv})")

print("\n=== 按 Agent 汇总 ===")
totals = defaultdict(lambda: [0, 0])
for agent, conv, port, inp, out in results:
    if inp >= 0:
        totals[agent][0] += 1
        totals[agent][1] += inp
for agent in sorted(totals):
    c, t = totals[agent]
    print(f"  {agent:<10}  convs={c:2d}  total_input={t:,}")

grand = sum(t[1] for t in totals.values())
print(f"\nGrand total input_tokens: {grand:,}")
