"""
Crawler de notícias — Portal de Pauta
=====================================
Busca RSS real dos portais configurados abaixo, pontua cada notícia com o
mesmo algoritmo de relevância usado no portal (palavras-chave, categoria e
cobertura cruzada entre fontes), filtra pelo piso de relevância e salva
tudo em noticias.json.

Por que RSS e não "raspar" a página HTML direto?
- É o próprio portal que disponibiliza aquele resumo pra ser reaproveitado
  (é literalmente pra isso que o RSS existe), então não há o problema de
  reproduzir texto protegido por direito autoral.
- É muito mais estável: o HTML de um site muda de layout sem aviso e
  quebra um scraper; um feed RSS raramente muda de formato.
- É mais rápido e mais leve tanto pra você quanto pro servidor do portal.

Como rodar localmente (antes de automatizar):
    pip install -r requirements.txt
    python crawler.py

Isso cria/atualiza o arquivo noticias.json na mesma pasta.
"""

import hashlib
import json
import re
from datetime import datetime, timezone

import feedparser

# ---------------------------------------------------------------------
# 1) FONTES
# ---------------------------------------------------------------------
FEEDS = [
    # -- direita --
    {"source": "Jovem Pan", "tipo": "direita", "category": "Política", "url": None},
    {"source": "Gazeta do Povo", "tipo": "direita", "category": "Política", "url": None},
    {"source": "Revista Oeste", "tipo": "direita", "category": "Política", "url": None},
    {"source": "Brasil Paralelo", "tipo": "direita", "category": "Costumes", "url": None},
    {"source": "Terça Livre", "tipo": "direita", "category": "Política", "url": None},
    {"source": "Pleno.News", "tipo": "direita", "category": "Política", "url": None},
    {"source": "Crusoé", "tipo": "direita", "category": "Política", "url": None},
    # -- mainstream (conteúdo real, filtrado pelo mesmo algoritmo) --
    {"source": "G1 - Política", "tipo": "mainstream", "category": "Política", "url": "https://g1.globo.com/rss/g1/politica/"},
    {"source": "G1 - Economia", "tipo": "mainstream", "category": "Economia", "url": "https://g1.globo.com/rss/g1/economia/"},
    {"source": "G1 - Mundo", "tipo": "mainstream", "category": "Internacional", "url": "https://g1.globo.com/rss/g1/mundo/"},
    {"source": "UOL", "tipo": "mainstream", "category": "Política", "url": "http://rss.home.uol.com.br/index.xml"},
    {"source": "Folha de São Paulo", "tipo": "mainstream", "category": "Política", "url": "https://feeds.folha.uol.com.br/emcimadahora/rss091.xml"},
    {"source": "CNN Brasil", "tipo": "mainstream", "category": "Política", "url": "https://admin.cnnbrasil.com.br/feed/"},
    {"source": "O Globo", "tipo": "mainstream", "category": "Política", "url": None},
    {"source": "Estadão", "tipo": "mainstream", "category": "Política", "url": "https://www.estadao.com.br/arc/outboundfeeds/rss/category/politica/"},
    {"source": "BBC Brasil", "tipo": "mainstream", "category": "Internacional", "url": "https://feeds.bbci.co.uk/portuguese/rss.xml"},
    {"source": "Poder360", "tipo": "mainstream", "category": "Política", "url": "https://www.poder360.com.br/feed/"},
]

# ---------------------------------------------------------------------
# 2) ALGORITMO DE RELEVÂNCIA — espelha exatamente o que está no portal
# ---------------------------------------------------------------------
CATEGORY_WEIGHT = {
    "Política": 95, "Justiça/STF": 92, "Economia": 68,
    "Segurança pública": 58, "Internacional": 55, "Costumes": 62,
}

KEYWORD_BANK = [
    ("STF", 95), ("eleições", 90), ("Congresso", 75), ("impeachment", 95),
    ("Bolsonaro", 92), ("anistia", 88), ("liberdade de expressão", 85),
    ("censura", 88), ("urnas", 78), ("esquerda", 60), ("segurança pública", 55),
    ("armamento", 60), ("família", 58), ("reforma tributária", 45),
    ("eleições 2026", 95), ("Flávio Bolsonaro", 95), ("Jair Bolsonaro", 92),
    ("Eduardo Bolsonaro", 88), ("Michelle Bolsonaro", 85), ("Tarcísio de Freitas", 88),
    ("Ronaldo Caiado", 78), ("Romeu Zema", 75), ("Nikolas Ferreira", 80),
    ("Renan Santos", 65), ("MBL", 60), ("pré-candidato", 70), ("candidatura", 65),
    ("convenção partidária", 55), ("TSE", 75), ("inelegível", 78),
    ("inelegibilidade", 78), ("Palácio do Planalto", 68), ("quarto mandato", 70),
    ("Lula", 82), ("polarização", 60),
]

RELEVANCE_FLOOR = 50
WEIGHTS = {"keywords": 0.35, "tags": 0.25, "impact": 0.25, "findability": 0.15}


def matched_keywords(title):
    title_lower = title.lower()
    return [(term, weight) for term, weight in KEYWORD_BANK if term.lower() in title_lower]


def score_keywords(title):
    hits = matched_keywords(title)
    if not hits:
        return 30
    return round(sum(w for _, w in hits) / len(hits))


def compute_relevance(item, cross_count):
    keywords_score = score_keywords(item["title"])
    hit_weights = [w for _, w in matched_keywords(item["title"])]
    tag_factor = max([CATEGORY_WEIGHT.get(item["category"], 45)] + hit_weights)
    impact = min(100, 25 + cross_count * 18)
    findability = min(100, 30 + len(hit_weights) * 20)
    score = (
        keywords_score * WEIGHTS["keywords"]
        + tag_factor * WEIGHTS["tags"]
        + impact * WEIGHTS["impact"]
        + findability * WEIGHTS["findability"]
    )
    return round(score)


def make_id(link):
    return hashlib.sha1(link.encode("utf-8")).hexdigest()[:12]


def collect():
    raw_items = []
    for feed in FEEDS:
        if not feed["url"]:
            print(f"[pendente] {feed['source']}: sem URL de RSS configurada, pulando.")
            continue
        try:
            parsed = feedparser.parse(feed["url"])
        except Exception as e:
            print(f"[erro] {feed['source']}: falha ao buscar feed ({e})")
            continue
        if parsed.bozo and not parsed.entries:
            print(f"[erro] {feed['source']}: feed inválido ou inacessível.")
            continue

        for entry in parsed.entries[:20]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "")
            if not title or not link:
                continue
            summary = re.sub("<[^<]+?>", "", entry.get("summary", "")).strip()
            published = entry.get("published", "") or entry.get("updated", "")
            raw_items.append({
                "id": make_id(link),
                "source": feed["source"],
                "tipo": feed["tipo"],
                "category": feed["category"],
                "title": title,
                "link": link,
                "summary": summary,
                "published_raw": published,
            })
    return raw_items


def cross_reference(raw_items):
    enriched = []
    for item in raw_items:
        my_keywords = {t for t, w in matched_keywords(item["title"]) if w >= 70}
        cross_sources = set()
        if my_keywords:
            for other in raw_items:
                if other["id"] == item["id"] or other["source"] == item["source"]:
                    continue
                other_keywords = {t for t, w in matched_keywords(other["title"]) if w >= 70}
                if my_keywords & other_keywords:
                    cross_sources.add(other["source"])
        enriched.append({**item, "cross_outlets": sorted(cross_sources)})
    return enriched


def build_output(enriched):
    output = []
    for item in enriched:
        score = compute_relevance(item, len(item["cross_outlets"]))
        if score < RELEVANCE_FLOOR:
            continue
        tags = [t for t, _ in matched_keywords(item["title"])][:5]
        output.append({
            "id": item["id"],
            "source": item["source"],
            "tipo": item["tipo"],
            "category": item["category"],
            "title": item["title"],
            "link": item["link"],
            "summary": item["summary"],
            "tags": tags,
            "crossOutlets": item["cross_outlets"],
            "score": score,
            "publishedRaw": item["published_raw"],
        })
    output.sort(key=lambda x: x["score"], reverse=True)
    return output


def main():
    raw_items = collect()
    print(f"Coletados {len(raw_items)} itens brutos de {sum(1 for f in FEEDS if f['url'])} feeds ativos.")
    enriched = cross_reference(raw_items)
    final = build_output(enriched)
    print(f"{len(final)} notícias passaram no piso de relevância ({RELEVANCE_FLOOR}).")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": final,
    }
    with open("noticias.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("Salvo em noticias.json")


if __name__ == "__main__":
    main()
