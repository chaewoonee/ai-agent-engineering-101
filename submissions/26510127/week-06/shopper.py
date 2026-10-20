"""Challenge 2: one buyer, a directory of shops, a shopping list, one total budget.

    python shopper.py --directory directory.json --list shopping_list.json --budget 600

directory.json : list of shop base URLs (their Agent Cards are read at start)
shopping_list.json : list of requests in the buyer's words, e.g. "a bookshelf for about 60 books"

How the state is carried (the core of the challenge):
  * the harness never sees a whole conversation. Each negotiation is kept as a short summary
    {want, shop, item, asks, offers, cap, status}; the full text goes only to the log.
  * every buyer move sends the harness: this negotiation's summary + a ledger of one line per list item
    (bought / open / waiting) + the budget left. The prompt stays about the same size however many shops talk.
  * the code, not the harness, enforces: the total never exceeds the budget (per-item caps that sum to at
    most the budget), each list item is bought at most once, every offer <= that item's cap.

Harness calls: 1 plan (which shops for which item, from the cards), 1 allocation (caps from opening asks),
then 1 per buyer move.
"""
import argparse
import asyncio
import json
import re
import time
import uuid

import httpx

from harness import ask_json

MAX_OFFERS = 6
QUOTES_PER_ITEM = 2

PLAN_PROMPT = """You are a buying agent with a shopping list and a directory of shops (their Agent Cards).
For each list entry pick up to 3 shops most likely to sell it, best first. Use only what the cards say.

Shops (index: name | description | tags):
{shops}

Shopping list (index: request):
{items}

Answer with one JSON object and nothing else, mapping list index to shop indexes:
{{"plan": {{"0": [3, 7], "1": [2]}}}}"""

ALLOC_PROMPT = """You are a buying agent. Total budget for the whole list: {budget}. Every entry must be bought.
Opening asks from the shops (list index: request -> shop: item, ask):
{quotes}

For each list entry choose one shop to negotiate with and a maximum price (cap) for that entry.
The caps must add up to at most {budget}. Leave room to bargain: shops usually come down from their opening ask.
Answer with one JSON object and nothing else:
{{"choice": {{"0": {{"shop": "<shop name>", "cap": <whole number>}}, "1": {{...}}}}}}"""

MOVE_PROMPT = """You are a buying agent negotiating one entry of a shopping list.

This negotiation: {summary}
Your hard cap for this entry is {cap}; never offer more. Pay as little as you can, but the shop may leave
after several low offers, so move toward its price in real steps. To accept the shop's price, offer exactly it.
Walk away only if the shop will not come down to {cap}.

Whole list (ledger): {ledger}
Budget left after deals so far: {left}

Answer with one JSON object and nothing else:
{{"action": "offer", "price": <whole number>}}
{{"action": "walk"}}"""


def last_number(text):
    nums = re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))
    return int(float(nums[-1])) if nums else None


class Shopper:
    def __init__(self, urls, wants, budget, log):
        self.urls, self.wants, self.budget, self.log = urls, wants, budget, log
        self.cards, self.neg = [], {}           # list idx -> summary dict of the negotiation in progress / done
        self.bought = {}                        # list idx -> {"shop", "item", "price"}
        self.lock = asyncio.Lock()
        self.prompt_sizes = []

    # ------------------------------------------------------------- A2A
    async def rpc(self, url, method, params):
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
        async with httpx.AsyncClient(timeout=200) as c:
            r = (await c.post(url, json=body, headers={"A2A-Version": "1.0"})).json()
        if "error" in r:
            raise RuntimeError(r["error"])
        return r["result"].get("task", r["result"])

    async def send(self, url, text, task_id=None, tag=""):
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        t = await self.rpc(url, "SendMessage", {"message": msg})
        said = " ".join(p.get("text", "") for p in t["status"].get("message", {}).get("parts", []))
        self.log(f"{tag} -> {text!r} <- {t['status']['state']} | {said}")
        return t, said

    async def harness(self, prompt, tag):
        self.prompt_sizes.append(len(prompt))
        move, raw = await asyncio.to_thread(ask_json, prompt)
        self.log(f"{tag} [harness {len(prompt)} chars] {raw}")
        return move

    # ------------------------------------------------------------- steps
    async def read_cards(self):
        async with httpx.AsyncClient(timeout=10) as c:
            for u in self.urls:
                card = (await c.get(u.rstrip("/") + "/.well-known/agent-card.json",
                                    headers={"A2A-Version": "1.0"})).json()
                self.cards.append(card)
        self.log(f"read {len(self.cards)} cards")

    async def plan(self):
        shops = "\n".join(f"{i}: {c['name']} | {c.get('description', '')} | "
                          f"{', '.join(t for s in c.get('skills', []) for t in s.get('tags', []))}"
                          for i, c in enumerate(self.cards))
        items = "\n".join(f"{i}: {w}" for i, w in enumerate(self.wants))
        move = await self.harness(PLAN_PROMPT.format(shops=shops, items=items), "[plan]")
        plan = {}
        for k, v in ((move or {}).get("plan") or {}).items():
            if str(k).isdigit() and int(k) < len(self.wants):
                plan[int(k)] = [s for s in v if isinstance(s, int) and 0 <= s < len(self.cards)][:3]
        for i in range(len(self.wants)):
            plan.setdefault(i, [])
            self.log(f"[plan] {i} {self.wants[i]!r} -> " + ", ".join(self.cards[s]["name"] for s in plan[i]))
        return plan

    async def open_quote(self, i, s):
        card = self.cards[s]
        url = card["supportedInterfaces"][0]["url"]
        tag = f"[{i}:{card['name']}]"
        try:
            t, said = await self.send(url, self.wants[i], tag=tag)
        except Exception as e:
            self.log(f"{tag} error {e}")
            return None
        if t["status"]["state"] != "TASK_STATE_INPUT_REQUIRED":
            return None
        m = re.search(r"The (.+?) is (\d+)\.", said)
        return {"i": i, "shop": card["name"], "url": url, "task": t["id"],
                "item": m.group(1) if m else said[:60], "asks": [last_number(said)], "offers": [], "status": "open"}

    async def allocate(self, quotes):
        lines = []
        for i in range(len(self.wants)):
            qs = quotes.get(i, [])
            lines.append(f"{i}: {self.wants[i]} -> " + ("; ".join(f"{q['shop']}: {q['item']}, {q['asks'][0]}"
                                                                     for q in qs) or "no quote"))
        move = await self.harness(ALLOC_PROMPT.format(budget=self.budget, quotes="\n".join(lines)), "[alloc]")
        choice = (move or {}).get("choice") or {}
        picked, caps = {}, {}
        for i, qs in quotes.items():
            c = choice.get(str(i)) or {}
            q = next((q for q in qs if q["shop"] == c.get("shop")), None) or min(qs, key=lambda q: q["asks"][0])
            picked[i] = q
            caps[i] = int(c.get("cap") or q["asks"][0])
        total = sum(caps.values())
        if total > self.budget:                       # code guard: caps must fit the budget
            scale = self.budget / total
            caps = {i: int(c * scale) for i, c in caps.items()}
            self.log(f"[guard] caps summed to {total} > budget {self.budget}; scaled by {scale:.2f} -> {caps}")
        for i, q in picked.items():
            q["cap"] = caps[i]
            self.log(f"[alloc] {i} {self.wants[i]!r}: {q['shop']} ({q['item']}), opening {q['asks'][0]}, cap {caps[i]}")
        return picked

    def ledger(self):
        out = []
        for i, w in enumerate(self.wants):
            if i in self.bought:
                b = self.bought[i]
                out.append(f"#{i} {w}: BOUGHT {b['item']} from {b['shop']} for {b['price']}")
            elif i in self.neg:
                n = self.neg[i]
                out.append(f"#{i} {w}: {n['status']} with {n['shop']}, last ask {n['asks'][-1]}, cap {n['cap']}")
            else:
                out.append(f"#{i} {w}: waiting")
        return " | ".join(out)

    def left(self):
        return self.budget - sum(b["price"] for b in self.bought.values())

    async def negotiate(self, q):
        i, tag = q["i"], f"[{q['i']}:{q['shop']}]"
        self.neg[i] = q
        t_state = "TASK_STATE_INPUT_REQUIRED"
        while t_state == "TASK_STATE_INPUT_REQUIRED" and len(q["offers"]) < MAX_OFFERS:
            summary = json.dumps({k: q[k] for k in ("shop", "item", "asks", "offers")} | {"want": self.wants[i]})
            move = await self.harness(MOVE_PROMPT.format(summary=summary, cap=q["cap"], ledger=self.ledger(),
                                                         left=self.left()), tag)
            if not move or move.get("action") != "offer" or not isinstance(move.get("price"), (int, float)):
                await self.rpc(q["url"], "CancelTask", {"id": q["task"]})
                q["status"] = "walked"
                self.log(f"{tag} walked away")
                return False
            offer = int(move["price"])
            async with self.lock:                     # code guards: cap, budget left, no double purchase
                limit = min(q["cap"], self.left())
                if i in self.bought:
                    self.log(f"{tag} [guard] #{i} already bought; stop")
                    return True
                if offer > limit:
                    self.log(f"{tag} [guard] offer {offer} above limit {limit} -> {limit}")
                    offer = limit
            q["offers"].append(offer)
            t, said = await self.send(q["url"], str(offer), q["task"], tag=tag)
            t_state = t["status"]["state"]
            if t_state == "TASK_STATE_INPUT_REQUIRED":
                q["asks"].append(last_number(said))
            elif t_state == "TASK_STATE_COMPLETED":
                price = None
                for a in t.get("artifacts", []):
                    for p in a.get("parts", []):
                        if isinstance(p.get("data"), dict):
                            price = int(p["data"].get("price"))
                async with self.lock:
                    self.bought[i] = {"shop": q["shop"], "item": q["item"], "price": price}
                q["status"] = "bought"
                self.log(f"{tag} BOUGHT {q['item']} for {price}")
                return True
        if t_state == "TASK_STATE_INPUT_REQUIRED":
            await self.rpc(q["url"], "CancelTask", {"id": q["task"]})
        q["status"] = "failed"
        return False

    async def run(self):
        t0 = time.time()
        await self.read_cards()
        plan = await self.plan()
        # opening quotes from the top shops of every entry, all at once
        jobs = [(i, s) for i, ss in plan.items() for s in ss[:QUOTES_PER_ITEM]]
        res = await asyncio.gather(*(self.open_quote(i, s) for i, s in jobs))
        quotes = {}
        for q in res:
            if q:
                quotes.setdefault(q["i"], []).append(q)
        picked = await self.allocate(quotes)
        for i, qs in quotes.items():                   # close the quotes we will not pursue
            for q in qs:
                if q is not picked[i]:
                    await self.rpc(q["url"], "CancelTask", {"id": q["task"]})
        done = await asyncio.gather(*(self.negotiate(q) for q in picked.values()))
        # entries that failed: retry once with the next shop in the plan and whatever budget is left
        for i in range(len(self.wants)):
            if i in self.bought:
                continue
            tried = {q["shop"] for q in quotes.get(i, [])}
            for s in plan.get(i, []):
                if self.cards[s]["name"] in tried:
                    continue
                q = await self.open_quote(i, s)
                if not q:
                    continue
                q["cap"] = self.left() - sum(self.neg[j]["cap"] for j in range(len(self.wants))
                                             if j not in self.bought and j != i and j in self.neg)
                self.log(f"[retry] #{i} with {q['shop']}, cap {q['cap']}")
                if q["cap"] > 0 and await self.negotiate(q):
                    break
        total = sum(b["price"] for b in self.bought.values())
        result = {"bought": {self.wants[i]: b for i, b in sorted(self.bought.items())},
                  "missing": [w for i, w in enumerate(self.wants) if i not in self.bought],
                  "total": total, "budget": self.budget,
                  "all_bought": len(self.bought) == len(self.wants), "within_budget": total <= self.budget,
                  "no_duplicates": len(self.bought) == len({i for i in self.bought}),
                  "harness_calls": len(self.prompt_sizes), "max_prompt_chars": max(self.prompt_sizes),
                  "mean_move_prompt_chars": int(sum(self.prompt_sizes[2:]) / max(1, len(self.prompt_sizes) - 2)),
                  "seconds": int(time.time() - t0)}
        self.log("RESULT " + json.dumps(result, ensure_ascii=False))
        return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--directory", required=True)
    ap.add_argument("--list", required=True)
    ap.add_argument("--budget", type=int, required=True)
    a = ap.parse_args()
    urls = json.load(open(a.directory, encoding="utf-8"))
    wants = json.load(open(a.list, encoding="utf-8"))
    s = Shopper(urls, wants, a.budget, lambda x: print(x, flush=True))
    asyncio.run(s.run())


if __name__ == "__main__":
    main()
