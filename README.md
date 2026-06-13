# Energetický radar ČR

Mobilní Streamlit dashboard pro sledování počasí relevantního pro obnovitelné zdroje a přípravu na ceny elektřiny a výrobu po zdrojích.

## Co už funguje

- výběr českých lokalit,
- vítr, nárazy větru, oblačnost a sluneční záření z Open-Meteo,
- jednoduché grafy,
- demonstrační cenový profil,
- mobilně přívětivý layout.

## Co je zatím demonstrační

- cena elektřiny,
- výroba elektřiny po zdrojích.

Tyto části jsou připravené pro pozdější napojení na OTE, ENTSO-E nebo ČEPS.

## Spuštění lokálně

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Nasazení na Streamlit Community Cloud

1. Vytvořit nový GitHub repozitář.
2. Nahrát soubory `app.py`, `requirements.txt` a `README.md`.
3. Otevřít Streamlit Community Cloud.
4. Vybrat repozitář.
5. Main file path: `app.py`.
6. Deploy.