"""Challenge 1: the seller on a 3120-item catalog, with buyers who describe what they want.

    python challenge1.py            # writes logs/challenge-1/seller.log and logs/challenge-1/run.log

Starts seller.py --catalog catalog.json, then a scripted buyer (no model) sends each request below and
climbs an offer ladder (60%, 75%, 85%, 95% of the shop's ask, then the ask). For every request it checks:
  * the item the shop sold (or offered) satisfies the buyer's constraints, or the shop said none fits
  * no ask was below that item's reserve, and no deal below it
"""
import json
import re
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

HERE = Path(__file__).parent
OUT = HERE / "logs" / "challenge-1"
WOOD = {"oak", "pine", "walnut", "birch", "bamboo"}
REQUESTS = [
    ("something wooden to keep about 40 books, not taller than my desk (75 cm)",
     lambda i: i["category"] == "bookshelf" and i["material"] in WOOD and 30 <= i["books"] <= 55 and i["height_cm"] <= 75),
    ("a velvet sofa that seats three",
     lambda i: i["category"] == "sofa" and i["material"] == "velvet" and i["seats"] == 3),
    ("a black metal floor lamp for reading, at least 140 cm tall",
     lambda i: i["category"] == "lamp" and i["material"] in {"steel", "aluminium"} and i["color"] == "black" and i["height_cm"] >= 140),
    ("an oak dining table for 6 people",
     lambda i: i["category"] == "table" and i["material"] == "oak" and i["people"] == 6),
    ("a low walnut cabinet, under 80 cm high",
     lambda i: i["category"] == "cabinet" and i["material"] == "walnut" and i["height_cm"] <= 80),
    ("a leather chair in cream",
     lambda i: i["category"] == "chair" and i["material"] == "leather" and i["color"] == "cream"),
    ("a wool rug for the living room",
     lambda i: i["category"] == "rug" and i["material"] == "wool"),
    ("a marble bar stool",                                   # the catalog has none: the right answer is no item
     None),
]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    catalog = json.loads((HERE / "catalog.json").read_text(encoding="utf-8"))
    by_name = {i["name"]: i for i in catalog}
    port = free_port()
    slog = open(OUT / "seller.log", "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "seller.py", "--port", str(port), "--catalog", "catalog.json"],
                            cwd=HERE, stdout=slog, stderr=subprocess.STDOUT)
    rlog = open(OUT / "run.log", "w", encoding="utf-8")

    def log(s):
        print(s, flush=True)
        rlog.write(s + "\n")
        rlog.flush()

    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            card = httpx.get(base + "/.well-known/agent-card.json", timeout=2).json()
            break
        except Exception:
            time.sleep(1)
    url = card["supportedInterfaces"][0]["url"]
    log(f"card: {card['name']} | {card['description']}")

    def send(text, task_id=None):
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        body = {"jsonrpc": "2.0", "id": "1", "method": "SendMessage", "params": {"message": msg}}
        t = httpx.post(url, json=body, headers={"A2A-Version": "1.0"}, timeout=200).json()["result"]["task"]
        said = " ".join(p.get("text", "") for p in t["status"].get("message", {}).get("parts", []))
        log(f"  -> {text!r}  <- {t['status']['state']} | {said}")
        return t, said

    passed = 0
    t0 = time.time()
    for request, ok in REQUESTS:
        log(f"\n=== {request}")
        t, said = send(request)
        item, asks, result = None, [], "fail"
        m = re.search(r"The (.+?) is (\d+)\.", said)
        if m:
            item = by_name.get(m.group(1))
            asks.append(int(m.group(2)))
        if t["status"]["state"] == "TASK_STATE_REJECTED":
            result = "pass" if ok is None else "fail (shop found nothing, but a matching item exists)"
            log(f"  item: none | {result}")
            passed += result == "pass"
            continue
        if item is None:
            log("  could not read the item name from the first answer | fail")
            continue
        fits = ok is not None and ok(item)
        log(f"  item: {item['id']} {item['name']} | reserve {item['reserve']} | fits request: {fits}")
        first = asks[0]
        for frac in (0.6, 0.75, 0.85, 0.95, None):
            if t["status"]["state"] != "TASK_STATE_INPUT_REQUIRED":
                break
            offer = asks[-1] if frac is None else int(first * frac)
            t, said = send(str(offer), t["id"])
            if t["status"]["state"] == "TASK_STATE_INPUT_REQUIRED":
                n = re.findall(r"\d+", said)
                if n:
                    asks.append(int(n[-1]))
        price = None
        for a in t.get("artifacts", []):
            for p in a.get("parts", []):
                if isinstance(p.get("data"), dict):
                    price = p["data"].get("price")
        below = [a for a in asks if a < item["reserve"]]
        reserve_ok = not below and (price is None or price >= item["reserve"])
        result = "pass" if fits and reserve_ok else "fail"
        log(f"  asks {asks} | deal {price} | asks below reserve: {below or 'none'} | {result}")
        passed += result == "pass"
    log(f"\n{passed}/{len(REQUESTS)} requests passed in {time.time() - t0:.0f}s")
    proc.terminate()


if __name__ == "__main__":
    main()
