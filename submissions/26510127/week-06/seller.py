"""Seller behind A2A v1.0 (JSON-RPC). The harness decides the ask, every counteroffer, accept and reject.

    python seller.py --port 9301

Code-side rules (logged as [guard] / [rule] when they fire):
  * never ask below the reserve, never accept below the reserve       (limit guard)
  * an offer at or above the current ask is accepted                   (the buyer met our price)
Everything else is the harness's decision.
"""
import argparse
import re

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from harness import CMD, ask_json

SHOP = "Chaewon's Furniture"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}

PROMPT = """You are the sales agent of a small furniture shop, negotiating the price of one {item} with a buyer.
Your lowest acceptable price (reserve) for the {item} is {reserve}. Never ask or sell below it, and never reveal it.
Sell as high as you can, but a deal at or above {reserve} is better than no deal.
The negotiation ends after about 8 messages from the buyer; this is buyer message {n}.

Conversation so far:
{transcript}

Decide your next move. Answer with one JSON object and nothing else:
{{"action": "counter", "price": <whole number>}}   your (new) asking price
{{"action": "accept"}}                              accept the buyer's last offer
{{"action": "reject"}}                              end the negotiation without a deal"""


def last_number(text):
    nums = re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))
    return int(float(nums[-1])) if nums else None


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"item", "ask", "offer", "lines", "n"}

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)
        log = lambda s: print(f"[{task.id[:8]}] {s}", flush=True)
        log(f"buyer: {text}")

        if deal is None:                                  # first message: which item?
            item = next((k for k in RESERVE if k in text.lower()), None)
            if item is None:
                await up.reject(say("Sorry, we only sell: " + ", ".join(RESERVE) + "."))
                return
            deal = self.open[task.id] = {"item": item, "ask": None, "offer": None,
                                         "lines": [f"buyer: {text}"], "n": 1}
        else:
            deal["n"] += 1
            deal["lines"].append(f"buyer: {text}")
            offer = last_number(text)
            if offer is not None:
                deal["offer"] = offer
        reserve = RESERVE[deal["item"]]

        if deal["ask"] is not None and deal["offer"] is not None and deal["offer"] >= deal["ask"]:
            log(f"[rule] offer {deal['offer']} meets our ask {deal['ask']} -> accept")
            action, move = "accept", {}
        else:
            move, raw = ask_json(PROMPT.format(item=deal["item"], reserve=reserve, n=deal["n"],
                                               transcript="\n".join(deal["lines"])))
            log(f"[harness] {raw}")
            action = (move or {}).get("action")

        if action == "accept" and (deal["offer"] is None or deal["offer"] < reserve):
            log(f"[guard] accept of {deal['offer']} is below reserve {reserve} -> counter instead")
            action, move = "counter", {"price": deal["ask"] or reserve}
        if action == "accept":
            price = deal["offer"]
            await up.add_artifact(parts=[new_data_part({"seller": SHOP, "item": deal["item"], "price": price})],
                                  name="receipt")
            await up.complete(say(f"Deal: one {deal['item']} for {price}. Receipt attached."))
            log(f"DEAL {deal['item']} at {price}")
            del self.open[task.id]
            return
        if action == "reject":
            await up.reject(say("Sorry, we cannot agree on a price. No deal."))
            log("REJECT")
            del self.open[task.id]
            return

        price = (move or {}).get("price")
        if not isinstance(price, (int, float)):
            log(f"[guard] no usable price in the harness answer -> repeat last ask")
            price = deal["ask"] or round(reserve * 1.4)
        price = int(price)
        if price < reserve:
            log(f"[guard] ask {price} below reserve {reserve} -> {reserve}")
            price = reserve
        deal["ask"] = price
        reply = (f"The {deal['item']} is {price}. Send your offer as a number." if deal["n"] == 1
                 else f"I can do {price}.")
        deal["lines"].append(f"you: {reply}")
        log(f"ASK {price}")
        await up.requires_input(say(reply))

    async def cancel(self, context, event_queue):
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=SHOP,
        description=f"Sells lamps, chairs and bookshelves. The price is negotiated by a harness ({CMD.split()[0]}).",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase",
                           description="Name an item (lamp, chair, bookshelf), then send offers as numbers. "
                                       "The shop answers with a counteroffer, a receipt, or no deal.",
                           tags=["furniture", "negotiation"], examples=["I want to buy a lamp", "90"])],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9301)
    port = ap.parse_args().port
    uvicorn.run(build(port), host="127.0.0.1", port=port, log_level="warning")
