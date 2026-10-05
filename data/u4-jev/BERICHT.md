# U4-Messbericht

Entscheidung: Fatih

## Ergebnisse je Auftrag

| Auftrag | lokal Top 5/10 | Jev Top 5/10 | übersehen | Fehlalarme | Grenzen | L1 Kontrolle | L2 Kontrolle |
|---|---:|---:|---:|---:|---:|---|---|
| A1 | 0/3 | 3/3 | 1 | 3 | 12 | {'noul': 0.06, 'bestanden': True} | {'noul': 0.06, 'bestanden': True} |
| A2 | 1/3 | 2/6 | 0 | 14 | 6 | {'noul': 0.07, 'bestanden': True} | {'noul': 0.07, 'bestanden': True} |
| A3 | 2/4 | 5/8 | 0 | 16 | 4 | {'noul': 0.06, 'bestanden': True} | {'noul': 0.07, 'bestanden': True} |
| A4 | 2/7 | 5/10 | 0 | 7 | 7 | {'noul': 0.03, 'bestanden': True} | {'noul': 0.04, 'bestanden': True} |
| A5 | 0/3 | 3/7 | 0 | 15 | 6 | {'noul': 0.05, 'bestanden': True} | {'noul': 0.05, 'bestanden': True} |
| **Summe L1** | **5/20** | **18/34** | **1** | **55** | **35** | — | — |

## Summe und Messläufe

Stabilität gepoolt: 0.9869281045751634 gleiche Seite, mittlere Δ 0.02084967320261438.
L1/L2 Kosten gesamt: $0.02933725; geschätzt: $0.02623648.
L1/L2 Laufzeit gesamt: 85.951 s.
L3 wie erwartet: True; Rohdatei: vorhanden.
Referenz-Commit: 567aa0c3dc5a16b192e0af937571e5b323d3dd17

### Pro Lauf

- L1/A1: Status bewertet; Modell jev-1.13.0; Laufzeit 8.347 s; Kosten $0.00313895 (geschätzt $0.00281933); Gegenprobe keine; Kontrolle {'noul': 0.06, 'bestanden': True}.
- L2/A1: Status bewertet; Modell jev-1.13.0; Laufzeit 8.392 s; Kosten $0.00313895 (geschätzt $0.00281933); Gegenprobe keine; Kontrolle {'noul': 0.06, 'bestanden': True}.
- L1/A2: Status bewertet; Modell jev-1.13.0; Laufzeit 8.397 s; Kosten $0.00296692 (geschätzt $0.00269942); Gegenprobe keine; Kontrolle {'noul': 0.07, 'bestanden': True}.
- L2/A2: Status bewertet; Modell jev-1.13.0; Laufzeit 8.432 s; Kosten $0.00296692 (geschätzt $0.00269942); Gegenprobe keine; Kontrolle {'noul': 0.07, 'bestanden': True}.
- L1/A3: Status bewertet; Modell jev-1.13.0; Laufzeit 8.612 s; Kosten $0.00284243 (geschätzt $0.00256582); Gegenprobe {'noul': 0.9, 'rang': 5}; Kontrolle {'noul': 0.06, 'bestanden': True}.
- L2/A3: Status bewertet; Modell jev-1.13.0; Laufzeit 9.011 s; Kosten $0.00284243 (geschätzt $0.00256582); Gegenprobe {'noul': 0.89, 'rang': 5}; Kontrolle {'noul': 0.07, 'bestanden': True}.
- L1/A4: Status bewertet; Modell jev-1.13.0; Laufzeit 8.922 s; Kosten $0.00267590 (geschätzt $0.00230555); Gegenprobe {'noul': 0.77, 'rang': 11}; Kontrolle {'noul': 0.03, 'bestanden': True}.
- L2/A4: Status bewertet; Modell jev-1.13.0; Laufzeit 8.380 s; Kosten $0.00267590 (geschätzt $0.00230555); Gegenprobe {'noul': 0.74, 'rang': 12}; Kontrolle {'noul': 0.04, 'bestanden': True}.
- L1/A5: Status bewertet; Modell jev-1.13.0; Laufzeit 8.758 s; Kosten $0.00304441 (geschätzt $0.00272811); Gegenprobe {'noul': 0.81, 'rang': 7}; Kontrolle {'noul': 0.05, 'bestanden': True}.
- L2/A5: Status bewertet; Modell jev-1.13.0; Laufzeit 8.701 s; Kosten $0.00304441 (geschätzt $0.00272811); Gegenprobe {'noul': 0.8, 'rang': 8}; Kontrolle {'noul': 0.05, 'bestanden': True}.

## Entscheidungsregel

- 1_jev_summe_top10_hoeher: ✔ — {"erfuellt": true, "jev": 34, "lokal": 20}
- 2_je_auftrag_maximal_einer_weniger: ✔ — {"erfuellt": true, "auftraege": {"A1": {"erfuellt": true, "jev_top10": 3, "lokal_top10": 3}, "A2": {"erfuellt": true, "jev_top10": 6, "lokal_top10": 3}, "A3": {"erfuellt": true, "jev_top10": 8, "lokal_top10": 4}, "A4": {"erfuellt": true, "jev_top10": 10, "lokal_top10": 7}, "A5": {"erfuellt": true, "jev_top10": 7, "lokal_top10": 3}}}
- 3_kontrolle_alle_laeufe_bestanden: ✔ — {"erfuellt": true, "laeufe": {"L1/A1": {"noul": 0.06, "bestanden": true}, "L2/A1": {"noul": 0.06, "bestanden": true}, "L1/A2": {"noul": 0.07, "bestanden": true}, "L2/A2": {"noul": 0.07, "bestanden": true}, "L1/A3": {"noul": 0.06, "bestanden": true}, "L2/A3": {"noul": 0.07, "bestanden": true}, "L1/A4": {"noul": 0.03, "bestanden": true}, "L2/A4": {"noul": 0.04, "bestanden": true}, "L1/A5": {"noul": 0.05, "bestanden": true}, "L2/A5": {"noul": 0.05, "bestanden": true}}}
- 4_stabilitaet_mindestens_0_90: ✔ — {"erfuellt": true, "wert": 0.9869281045751634}
- 5_l3_wie_erwartet: ✔ — {"erfuellt": true, "anzahl": 30, "status_gesamt": "nicht_bewertet", "gruende_http_401": 30}
- Alle erfüllt: ✔
