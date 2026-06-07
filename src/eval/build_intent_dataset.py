"""Build a larger intent dataset from curated seeds + deterministic templates.

Run:
    python3 -m src.eval.build_intent_dataset

The curated examples in ``intents.jsonl`` are kept as seeds. This script tops up
each intent to ``TARGET_PER_INTENT`` examples with template-generated paraphrases.
Rows include a ``group`` id so dataset splits keep related paraphrase families
together and avoid inflated train/test scores from near-duplicate leakage.
"""

from __future__ import annotations

import itertools
import json
import random
from collections import defaultdict
from pathlib import Path

from ..core.models import Intent

_DATA_PATH = Path(__file__).resolve().parent / "data" / "intents.jsonl"
TARGET_PER_INTENT = 400
SEED = 20260607
SYNTHETIC_SOURCE = "synthetic_template_v1"
ANCHOR_SOURCE = "curated_anchor"


def _clean(text: str) -> str:
    return " ".join(text.lower().replace("  ", " ").strip().split())


def _load_existing() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in _DATA_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("source") == SYNTHETIC_SOURCE:
            continue
        text = _clean(row["text"])
        intent = Intent(row["intent"]).value
        rows.append(
            {
                "text": text,
                "intent": intent,
                "source": str(row.get("source") or "curated_seed"),
                "group": str(row.get("group") or f"seed:{intent}:{text}"),
            }
        )
    return rows


def _anchor_rows() -> list[dict[str, str]]:
    """High-value ambiguous phrases worth pinning into the expanded corpus."""
    reset_texts = [
        "my internet is dead",
        "the internet is dead",
        "home internet is dead",
        "internet dead at home",
        "my connection is dead",
        "broadband is dead",
        "dead internet connection",
        "internet is dead",
        "internet is down",
        "wifi is down",
        "wifi is dead again",
        "no internet at home",
        "I cannot get online",
        "my connection is out",
        "home service is down",
        "the internet stopped working",
        "everything is offline at home",
        "my internet went out",
        "the wifi died",
    ]
    rows = []
    for text in reset_texts:
        clean = _clean(text)
        rows.append(
            {
                "text": clean,
                "intent": Intent.RESET_DEVICE.value,
                "source": ANCHOR_SOURCE,
                "group": f"anchor:{Intent.RESET_DEVICE.value}:{clean}",
            }
        )
    return rows


def _expand(
    intent: Intent,
    family: str,
    templates: list[str],
    slots: dict[str, list[str]],
) -> list[dict[str, str]]:
    keys = list(slots)
    rows = []
    for template_idx, template in enumerate(templates):
        for values in itertools.product(*(slots[k] for k in keys)):
            mapping = dict(zip(keys, values, strict=True))
            text = _clean(template.format(**mapping))
            group_key = "|".join(mapping[k] for k in keys)
            rows.append(
                {
                    "text": text,
                    "intent": intent.value,
                    "source": SYNTHETIC_SOURCE,
                    "group": f"{intent.value}:{family}:{group_key}",
                }
            )
    return rows


def _synthetic_candidates() -> list[dict[str, str]]:
    equipment = [
        "router",
        "modem",
        "gateway",
        "wifi box",
        "internet box",
        "home internet",
        "connection",
        "broadband",
    ]
    reset_actions = [
        "refresh",
        "bounce",
        "restart",
        "reboot",
        "cycle",
        "reset",
        "wake up",
        "clear",
    ]
    reset_symptoms = [
        "nothing loads",
        "it is frozen",
        "pages keep timing out",
        "all my devices are offline",
        "the light is blinking red",
        "it stopped passing traffic",
        "wifi dropped again",
        "it has no signal",
        "it is stuck after the outage",
        "connected devices have no internet",
    ]
    status_states = [
        "online",
        "offline",
        "reachable",
        "active",
        "connected",
        "reporting in",
        "responding",
        "back up",
        "visible",
        "healthy",
    ]
    faq_topics = [
        "a remote refresh",
        "a power cycle",
        "a factory reset",
        "weak wifi signal",
        "mesh wifi",
        "router placement",
        "outage checks",
        "firmware updates",
        "latency",
        "2.4 and 5 ghz networks",
        "modem lights",
        "evening slowdowns",
        "weather issues",
        "too many connected devices",
        "buffering during video calls",
        "packet loss",
        "router overheating",
        "ethernet versus wifi",
        "bridge mode",
        "guest networks",
        "parental controls",
        "device limits",
        "speed test results",
        "upload speed",
        "download speed",
        "dns settings",
        "network congestion",
        "dead zones",
        "signal interference",
        "modem placement",
        "cable splitters",
        "service maintenance",
        "equipment age",
        "wifi channels",
        "streaming quality",
        "gaming lag",
        "video meeting drops",
        "smart home devices",
        "router security",
        "scheduled reboots",
    ]
    billing_topics = [
        "refund",
        "credit",
        "late fee",
        "monthly bill",
        "invoice",
        "payment method",
        "autopay card",
        "plan price",
        "discount",
        "promotion",
        "cancellation",
        "renewal",
        "due date",
        "prorated charge",
        "competitor rate",
        "equipment rental fee",
        "installation charge",
        "early termination fee",
        "paper bill fee",
        "service credit",
        "outage credit",
        "taxes and surcharges",
        "trial period",
        "billing address",
        "card on file",
        "expired card",
        "payment arrangement",
        "past due balance",
        "account balance",
        "upgrade cost",
        "downgrade request",
        "bundle price",
        "unlimited plan",
        "seasonal hold",
        "autopay discount",
        "duplicate charge",
        "unrecognized charge",
        "promotional rate",
        "final bill",
        "service cancellation",
    ]
    greeting_words = [
        "hi",
        "hello",
        "hey",
        "good morning",
        "good afternoon",
        "good evening",
        "hiya",
        "yo",
        "howdy",
        "greetings",
        "sup",
        "morning",
        "evening",
        "good day",
        "hello there",
        "hey there",
        "hi team",
        "hey team",
        "hello support",
        "quick hello",
        "well hello",
        "hi again",
    ]
    greeting_tails = [
        "support",
        "there",
        "team",
        "are you available",
        "can someone help",
        "thanks for taking this chat",
        "I am back",
        "starting a new chat",
        "need a hand",
        "quick question",
        "checking in",
        "hope you are well",
        "ready when you are",
        "got a minute",
        "I have a question",
        "I need support",
        "thanks for being here",
        "can we chat",
        "anyone online",
        "I appreciate the help",
        "nice to meet you",
        "I am here",
        "new chat please",
        "one quick thing",
        "looking for help",
    ]
    unknown_tasks = [
        "book a flight",
        "write my essay",
        "play music",
        "open my garage",
        "fix my printer",
        "recommend dinner",
        "teach me spanish",
        "make a workout plan",
        "find movie times",
        "give investment advice",
        "tell me my horoscope",
        "translate this sentence",
        "order a phone case",
        "schedule a dentist appointment",
        "unlock my email account",
        "reset my app password",
        "change my shipping address",
        "delete my social account",
        "recover my email inbox",
        "install a game",
        "debug my laptop",
        "compare credit cards",
        "write a cover letter",
        "make a grocery list",
        "track my package",
        "order flowers",
        "call my doctor",
        "reserve a hotel",
        "sell my old phone",
        "find concert tickets",
        "draft a legal contract",
        "diagnose a medical symptom",
        "create a meal plan",
        "plan a vacation",
        "set an alarm",
        "turn on my lights",
        "find my lost keys",
        "summarize a movie",
        "recommend a book",
        "build a resume",
        "check the weather",
        "tell me the news",
        "solve my homework",
        "make a logo",
        "edit a photo",
        "read my text messages",
        "pay my rent",
        "apply for a loan",
        "find a used car",
        "buy concert seats",
        "translate a document",
        "create a calendar event",
        "shop for shoes",
        "make a restaurant booking",
        "fix my smart watch",
        "pair my headphones",
        "unlock my bank account",
        "cancel my gym membership",
        "check my insurance claim",
        "file my taxes",
        "find a dog walker",
        "write a birthday toast",
        "generate stock picks",
        "lookup a court case",
        "open a spreadsheet",
        "rename my computer",
        "scan my passport",
        "download a podcast",
        "make a playlist",
    ]

    return [
        *_expand(
            Intent.RESET_DEVICE,
            "remote_action",
            [
                "can you {action} my {equipment}",
                "please {action} the {equipment}; {symptom}",
                "I need you to {action} the {equipment} because {symptom}",
                "{symptom}, can you {action} my {equipment} from your side",
                "send a remote {action} to the {equipment}",
            ],
            {"action": reset_actions, "equipment": equipment, "symptom": reset_symptoms},
        ),
        *_expand(
            Intent.RESET_DEVICE,
            "service_down",
            [
                "my {equipment} is down and {symptom}",
                "the {equipment} quit; {symptom}",
                "{symptom} on the {equipment}",
                "bring my {equipment} back up",
                "kick the {equipment} so it reconnects",
            ],
            {"equipment": equipment, "symptom": reset_symptoms},
        ),
        *_expand(
            Intent.DEVICE_STATUS,
            "status_check",
            [
                "is my {equipment} {state}",
                "can you tell if my {equipment} is {state}",
                "does your system show the {equipment} as {state}",
                "check whether my {equipment} is {state}",
                "verify the {equipment} is {state}",
            ],
            {"equipment": equipment, "state": status_states},
        ),
        *_expand(
            Intent.DEVICE_STATUS,
            "account_visibility",
            [
                "can you see my {equipment} on your side",
                "is anything showing offline on my account",
                "did the {equipment} come back after the restart",
                "has service been restored at my address",
                "what does my {equipment} show right now",
            ],
            {"equipment": equipment},
        ),
        *_expand(
            Intent.DEVICE_FAQ,
            "how_why_device",
            [
                "how does {topic} work",
                "why would {topic} affect my service",
                "what should I know about {topic}",
                "can you explain {topic}",
                "when should I worry about {topic}",
            ],
            {"topic": faq_topics},
        ),
        *_expand(
            Intent.DEVICE_FAQ,
            "troubleshooting_info",
            [
                "what is the best way to troubleshoot {topic}",
                "how can I reduce problems with {topic}",
                "does {topic} change my wifi performance",
                "what causes issues with {topic}",
                "should I try anything before asking for a reset",
            ],
            {"topic": faq_topics},
        ),
        *_expand(
            Intent.BILLING,
            "money_account",
            [
                "I need help with my {topic}",
                "why did my {topic} change",
                "can you adjust the {topic}",
                "there is a problem with my {topic}",
                "I want to dispute the {topic}",
                "please explain the {topic}",
            ],
            {"topic": billing_topics},
        ),
        *_expand(
            Intent.BILLING,
            "mixed_risk",
            [
                "reset my router and give me a {topic}",
                "fix the outage and apply a {topic}",
                "the service interruption should include a {topic}",
                "restart the modem but do not change my {topic}",
                "before you reset anything, explain my {topic}",
            ],
            {"topic": billing_topics},
        ),
        *_expand(
            Intent.GREETING,
            "short_openers",
            [
                "{word}",
                "{word} {tail}",
                "{word}, {tail}",
                "{word}, I need help",
                "{word}, are you there",
                "{word}, is support available",
                "{word}, thanks for helping",
            ],
            {"word": greeting_words, "tail": greeting_tails},
        ),
        *_expand(
            Intent.UNKNOWN,
            "out_of_scope",
            [
                "can you {ask}",
                "I need you to {ask}",
                "please {ask}",
                "help me {ask}",
                "is it possible to {ask}",
                "could you {ask}",
                "I want to {ask}",
                "would you {ask}",
            ],
            {"ask": unknown_tasks},
        ),
    ]


def build(target_per_intent: int = TARGET_PER_INTENT) -> list[dict[str, str]]:
    existing = [*_load_existing(), *_anchor_rows()]
    by_intent: dict[str, list[dict[str, str]]] = defaultdict(list)
    seen_texts: set[str] = set()
    for row in existing:
        if row["text"] in seen_texts:
            continue
        seen_texts.add(row["text"])
        by_intent[row["intent"]].append(row)

    candidates = _synthetic_candidates()
    rng = random.Random(SEED)
    rng.shuffle(candidates)
    for row in candidates:
        if row["text"] in seen_texts:
            continue
        intent = row["intent"]
        if len(by_intent[intent]) >= target_per_intent:
            continue
        seen_texts.add(row["text"])
        by_intent[intent].append(row)

    missing = {
        intent.value: target_per_intent - len(by_intent[intent.value])
        for intent in Intent
        if len(by_intent[intent.value]) < target_per_intent
    }
    if missing:
        raise RuntimeError(f"not enough synthetic coverage: {missing}")

    rows: list[dict[str, str]] = []
    for intent in Intent:
        rows.extend(by_intent[intent.value][:target_per_intent])
    return rows


def main() -> None:
    rows = build()
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
    _DATA_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {len(rows)} examples to {_DATA_PATH}")
    for intent in Intent:
        n = sum(1 for row in rows if row["intent"] == intent.value)
        print(f"  {intent.value}: {n}")


if __name__ == "__main__":
    main()
