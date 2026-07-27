import json

with open("designs/osifog_level3/phase1_results.json") as f:
    d = json.load(f)

legal = [x for x in d if x['score']['is_legal']]
print(f"Legal configs: {len(legal)}")

if legal:
    best_legal = max(legal, key=lambda x: x['score']['score'])
    print(f"Best legal score: {best_legal['score']['score']}")
else:
    print("NO LEGAL CONFIGS!")

best_raw = max(d, key=lambda x: x['score']['raw_score'])
print(f"Best raw score: {best_raw['score']['raw_score']}")
print(f"Violations of best raw: {best_raw['score']['violations']}")
