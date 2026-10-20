"""Challenge 2: start 20 copies of seller.py with different catalogs and prices, then run shopper.py.

    python challenge2.py           # logs/challenge-2/{shops.json, directory.json, shopping_list.json, shopper.log, shop-*.log}
"""
import json
import random
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent
OUT = HERE / "logs" / "challenge-2"
SHOPPING_LIST = [
    "a reading lamp for my desk",
    "a comfortable office chair",
    "a bookshelf for about 60 books",
    "a small wool rug for the bedroom",
    "a mirror for the hallway",
]
BUDGET = 650
NAMES = ["Oak & Ash", "Lumen House", "Seat Works", "Paper Shelf", "Hallway Goods", "Loom Rugs", "North Studio",
         "Urban Nest", "Brightside", "Timber Lane", "The Reading Room", "Cozy Corner", "Glass & Steel", "Rattan Row",
         "Second Home", "Little Loft", "Maple Street", "Quiet Rooms", "Coast Living", "Heritage Hall"]
CATS = ["bookshelf", "desk", "chair", "lamp", "sofa", "table", "bed", "wardrobe", "rug", "stool", "cabinet", "mirror"]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(20)
    shops, procs = [], []
    for name in NAMES:
        cats = rnd.sample(CATS, rnd.randint(2, 4))
        markup = round(rnd.uniform(0.9, 1.35), 2)
        port = free_port()
        shops.append({"name": name, "categories": cats, "markup": markup, "port": port,
                      "url": f"http://127.0.0.1:{port}"})
        log = open(OUT / f"shop-{port}.log", "w", encoding="utf-8")
        procs.append(subprocess.Popen([sys.executable, "seller.py", "--port", str(port), "--catalog", "catalog.json",
                                       "--categories", ",".join(cats), "--markup", str(markup), "--name", name],
                                      cwd=HERE, stdout=log, stderr=subprocess.STDOUT))
    for s in shops:
        for _ in range(60):
            try:
                httpx.get(s["url"] + "/.well-known/agent-card.json", timeout=2)
                break
            except Exception:
                time.sleep(1)
    (OUT / "shops.json").write_text(json.dumps(shops, indent=1), encoding="utf-8")
    (OUT / "directory.json").write_text(json.dumps([s["url"] for s in shops], indent=1), encoding="utf-8")
    (OUT / "shopping_list.json").write_text(json.dumps(SHOPPING_LIST, indent=1), encoding="utf-8")
    for want in SHOPPING_LIST:
        print(want, "<-", [s["name"] for s in shops if any(c in want for c in s["categories"])])
    try:
        with open(OUT / "shopper.log", "w", encoding="utf-8") as f:
            subprocess.run([sys.executable, "shopper.py", "--directory", str(OUT / "directory.json"),
                            "--list", str(OUT / "shopping_list.json"), "--budget", str(BUDGET)],
                           cwd=HERE, stdout=f, stderr=subprocess.STDOUT, timeout=1500)
    finally:
        for p in procs:
            p.terminate()
    print((OUT / "shopper.log").read_text(encoding="utf-8").splitlines()[-1])


if __name__ == "__main__":
    main()
