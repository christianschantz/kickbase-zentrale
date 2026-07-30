# Kompatibilitäts-Wrapper: alte Importe funktionieren weiter,
# neue Logik liegt in scoring.py / squad_analysis.py / bid_advisor.py
from scoring import score_player, explain  # noqa
