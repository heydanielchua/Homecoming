"""Mirror the data.gov.sg catalogue, because its search is broken.

Usage:  python scripts/refresh_catalogue.py [out.csv]


query=, q=, search=, keyword=, name=, title= and agencies= are all ignored by
the public API, which returns the catalogue alphabetically whatever you ask
it. There is no search endpoint on api-open either. So the only way to find a
dataset id is to hold a copy of the index.
"""
import csv, sys, requests, concurrent.futures as cf

BASE = "https://api-production.data.gov.sg/v2/public/api/datasets"
OUT = sys.argv[1] if len(sys.argv) > 1 else "data/datagov_catalogue.csv"
sess = requests.Session()


def page(n, tries=3):
    for _ in range(tries):
        try:
            r = sess.get(BASE, params={"page": n}, timeout=30)
            if r.ok:
                return r.json().get("data", {}).get("datasets") or []
        except requests.RequestException:
            pass
    return []


head = sess.get(BASE, params={"page": 1}, timeout=30).json().get("data", {})
pages = head.get("pages")
print(f"catalogue: {head.get('totalRowCount')} datasets over {pages} pages")

rows = list(head.get("datasets") or [])
with cf.ThreadPoolExecutor(max_workers=4) as ex:
    for got in ex.map(page, range(2, pages + 1)):
        rows.extend(got)

seen, keep = set(), []
for d in rows:
    i = d.get("datasetId")
    if i and i not in seen:
        seen.add(i)
        keep.append({
            "datasetId": i,
            "name": (d.get("name") or "").strip(),
            "agency": (d.get("managedByAgencyName") or "").strip(),
            "format": d.get("format") or "",
            "lastUpdatedAt": d.get("lastUpdatedAt") or "",
        })

keep.sort(key=lambda r: (r["agency"], r["name"]))
with open(OUT, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(keep[0].keys()))
    w.writeheader()
    w.writerows(keep)
print(f"indexed {len(keep)} datasets -> {OUT}")
