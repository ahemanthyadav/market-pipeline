"""Daily sentiment scoring (TX2): FinBERT over unscored raw_headlines.

Reads WHERE scored=FALSE (up to 1000 newest), runs ProsusAI/finbert batched
CPU inference, writes news_sentiment rows, marks headlines scored. Score map:
positive -> +p, negative -> -p, neutral -> 0.0. ticker_relevance is a cheap
keyword tag (sector/index match, else 'general') — Grafana aggregates the rest.

CPU inference on the TX2's ARM cores is minutes for a daily batch; no CUDA
build needed (JetPack 4.6.1's CUDA 10.2 / Python 3.6 era is sidestepped with a
python:3.11 container + PyTorch CPU aarch64 wheels). GPU/TensorRT remains an
optional future optimization.

Exit 0 when there is nothing to score (normal on re-runs).

Env: PGHOST, PGUSER, PGPASSWORD, PGDATABASE, BATCH_SIZE (default 16).
"""

import os
import sys

import psycopg
from transformers import pipeline

MODEL = "ProsusAI/finbert"
# The commit baked into the image (images/ml/Dockerfile FINBERT_REVISION — keep equal).
# Unpinned, a rebuild could silently change the weights behind every sentiment score.
MODEL_REVISION = "0bc2ac10dda6929bcb1f77ccc43c7ec5c424f031"
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "16"))
MAX_ROWS = 1000

RELEVANCE_KEYWORDS = {
    "NIFTY": ["nifty", "nifty 50"],
    "SENSEX": ["sensex"],
    "BANKNIFTY": ["bank nifty", "banknifty"],
    "BANK": ["bank ", "banks", "hdfc", "icici", "sbi"],
    "IT": ["infosys", "tcs", "wipro", "hcl", "it stocks", "tech mahindra"],
    "AUTO": ["maruti", "tata motors", "mahindra", "auto stocks", "hero moto"],
    "PHARMA": ["pharma", "sun pharma", "cipla", "dr reddy"],
    "FMCG": ["fmcg", "hul", "nestle india", "itc ", "dabur"],
    "METAL": ["metal", "tata steel", "hindalco", "jsw steel"],
    "ENERGY": ["oil", "crude", "ongc", "reliance", "ntpc", "power grid"],
    "GOLD": ["gold", "silver"],
    "INDIAVIX": ["vix", "volatility"],
}


def tag_relevance(headline: str) -> str:
    lowered = headline.lower()
    for tag, keywords in RELEVANCE_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            return tag
    return "general"


def score_label(result: dict) -> float:
    label = result["label"].lower()
    score = float(result["score"])
    if label.startswith("pos"):
        return score
    if label.startswith("neg"):
        return -score
    return 0.0


def main() -> int:
    conn = psycopg.connect(
        host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, headline, source, ts FROM raw_headlines "
                "WHERE scored = FALSE ORDER BY ts DESC LIMIT %s",
                (MAX_ROWS,),
            )
            pending = cur.fetchall()
    if not pending:
        print("NO-WORK: nothing unscored", flush=True)
        return 0
    print(f"scoring {len(pending)} headlines", flush=True)

    clf = pipeline(
        "sentiment-analysis",
        model=MODEL,
        tokenizer=MODEL,
        revision=MODEL_REVISION,
        truncation=True,
        max_length=512,
    )
    texts = [row[1] for row in pending]
    results = clf(texts, batch_size=BATCH_SIZE)

    scored = 0
    conn2 = psycopg.connect(
        host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )
    with conn2:
        with conn2.cursor() as cur:
            for (row_id, headline, source, ts), res in zip(pending, results):
                if isinstance(res, list):
                    res = res[0]
                cur.execute(
                    "INSERT INTO news_sentiment "
                    "(headline, source, ts, sentiment_score, ticker_relevance) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (headline[:500], source, ts, score_label(res),
                     tag_relevance(headline)),
                )
                cur.execute(
                    "UPDATE raw_headlines SET scored = TRUE WHERE id = %s",
                    (row_id,),
                )
                scored += 1
    conn.close()
    conn2.close()
    print(f"scored {scored} headlines into news_sentiment", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
