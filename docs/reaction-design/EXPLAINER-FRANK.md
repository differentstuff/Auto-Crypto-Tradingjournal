# Reaktionsnetzwerk

Dein System läuft als Pipeline: A → B → C → D → E → F → G. Jeder Agent hat seinen Platz, die Daten fliessen von oben nach unten. 
Wenn Schritt C nichts zu tun hat, läuft die Pipeline trotzdem weiter, oder bricht ab.

Was ich geändert habe:
Statt A→B→C→D→E→F→G entscheidet das System selbst, welche Schritte nötig sind. 
Es kann Schritte überspringen, zurückgehen, oder einfach warten. A→B→G→M→Z. Oder nur A→Wait.

Ich habe das Gerüst ersetzt. Der Motor ist deiner. 90% des Codes sind deine Module. 
Die Mathematik, die Indikatoren, die Exchange-Clients, die Datenbank, die LLM-Anbindung - alles dein Zeug. 
Ich habe die Architektur neu gedacht, nicht die Implementierung neu geschrieben.

Das ist ein Architekturvorschlag, kein fertiges System. 
Die Doku unter docs/reaction-design/ beschreibt das Ziel, nicht den aktuellen Stand. 
Diverse Punkte müssen noch ausgebessert/implementiert werden.


## Die fünf Bausteine

### 1. Substrat (Der gemeinsame Datenhaufen)

Statt dass jeder Agent seine TypedDict-Contracts hat und Daten von Agent zu Agent weitergibt, gibt es ein einziges Substrat. 
Ein zentraler Datenhaufen, auf den alle zugreifen. 
Wie ein Fileshare: niemand macht sich eine lokale Kopie. 
Wenn CollectOHLCV neue Indikatoren schreibt, sieht ScoreConfluence sie sofort.

Vorteil: Keine Contract-Brüche. Kein «Agent 3 erwartet Feld X, aber Agent 2 liefert Feld Y».
Nachteil: Single Point of Failure. Wenn das Substrat korrumpiert ist, ist alles korrumpiert. 
Deshalb gibt es ISC (siehe Punkt 4).

### 2. Enzyme (Module, die nur feuern, wenn die Form passt)

Enzyme sind keine Agenten. Ein Agent hat Autonomie. Ein Enzym hat Bedingungen. 
Wenn die nicht stimmen, kann es gar nicht erst feuern. 
Und wenn es feuert, kommt immer der gleiche Output für den gleichen Input.

Wie das Spiel mit den Formen als Kind: Das Dreieck passt nur ins dreieckige Loch. 
Wenn die Bedingungen nicht stimmen (Loch = Dreieck, aber Enzym = Viereck), dann passiert nichts. 
Und wenn es passt, verarbeitet es immer ein Dreieck - nie ein Viereck oder einen Kreis.

Jedes Enzym hat `requires` (was im Substrat stehen muss), `prohibits` (was nicht stehen darf), und `output` (was es schreibt). 
Fehlt auch nur eine `requires`-Bedingung, feuert es nicht. Punkt.

Beispiel ApproveTrade: braucht candidates nicht leer, noise_flag == false, entry_zones vorhanden, available_margin ausreichend, offene Positionen < max. 
Fehlt eins? Kein Trade. Kein Fallback, kein «vielleicht trotzdem».

### 3. Flux Scoring (Welches Enzym ist am wichtigsten?)

Das System feuert nicht einfach irgendein Enzym, das gerade aktiv werden kann. 
Es berechnet für jedes aktive Enzym einen Flux Score: Wie viel bringt uns dieses Enzym dem Ziel näher?

So funktioniert das: Simuliere die Transformation (was wäre das Substrat nach diesem Enzym?), messe den Gradienten vorher und nachher, und das Enzym mit dem höchsten positiven Score feuert. Ausser ein Regulator (RiskManager) ist aktivierbar - der hat immer Vorrang.

Das ist der Kernunterschied zur Pipeline: Das System priorisiert intelligent. 
Es macht immer den Schritt, der den meisten Fortschritt bringt. 
Aber es braucht einen gut definierten Attraktor, sonst weiss es nicht: «näher an was?».

### 4. ISC (Ideal State Criteria)

ISC steht für Ideal State Criteria (von Daniel Miessler, [TheAlgorithm](https://github.com/danielmiessler/TheAlgorithm)). 
Kriterien, die den Idealzustand definieren. 
Sie sind wie Schalter: erfüllt oder nicht erfüllt. Kein «fast erfüllt», kein «nur diesmal».

Die Idee stammt von David Deutsch: Eine gute Erklärung ist hard-to-vary. 
Du kannst sie nicht leicht abändern, ohne dass sie kaputtgeht. 
ISC übertragen das auf Zustände: Ein gutes Kriterium ist hard-to-vary - du kannst es nicht leicht uminterpretieren.

Unsere ISC-Einträge:
- ISC-001: Entry-Threshold muss erreicht sein
- ISC-002: Stop-Loss muss gesetzt sein
- ISC-003: Positionsgrösse innerhalb des Risiko-Limits
- ISC-004: Maximale offene Positionen nicht überschritten
- ISC-005: Kein Trade bei noise_flag
- ISC-006: Mindestanzahl Indikatoren ausgerichtet
- ISC-007: Pre-Trade-Trajektorie kein sudden coincidence

Jedes Mal wenn ein Enzym feuert, werden die ISC geprüft. 
Wenn ISC-002 nicht verifiziert werden kann, kann ExecuteTrade nicht feuern - egal was ApproveTrade sagt. 
Strukturell unmöglich, einen Trade ohne SL zu eröffnen.

Nachteil: ISC sind starr. Eine zu restriktive Bedingung blockiert legitime Trades. 
Und neue Bedingungen brauchen neue Operatoren im Code.

### 5. Attraktor (Das Ziel, das alle zieht)

Der Attraktor ist der Zustand, auf den alles hinarbeitet. 
Nicht «Schritt 7 der Pipeline ist erreicht», sondern «der Substrat-Zustand erfüllt diese Bedingungen».

Unser Attraktor: Trades eröffnen, die der Strategie entsprechen, und die Strategie lernt dazu und wird profitabler.
Sub-Attraktoren: `watching` (Standard, keine Signale), `trade_opened` (Position erstellt, ISC erfüllt), `trade_managed` (Position überwacht), `trade_closed` (Ergebnis erfasst), `learning_updated` (Accuracy aktualisiert).

Der Attraktor gibt dem Flux Scoring ein Ziel. Ohne ihn wüsste das System nicht, in welche Richtung es gehen soll.


## Was das ermöglicht (was die Pipeline nicht kann)

Graceful Degradation. 
In der Pipeline: Agent 3 liefert None, die Kette bricht. 
Im Reaktionsnetzwerk: Kein Enzym kann feuern? System geht in Wait. 
Und Wait ist kein Fehler, es ist der gesunde Ruhezustand. «Der Markt schuldet uns nichts.»

Selbstverbesserung. 
Dein Hindsight-Scoring und Rulebook, portiert und erweitert: Per-Signal Accuracy («RSI lag in 71% der Fälle richtig. MACD solo nur 50% - unterdrücken.»), Kombinationen («RSI+MACD beide bullish = 83% Win-Rate, p<0.01»), Trajektorie («Graduelle Ausrichtung über 6+ Bars gewinnt 78%. Sudden Snap verliert 67%.»), Idle Cycles («Bei VIX>30 war Abwarten in 80% der Fälle richtig»). 
Die statistische Strenge (Wilson Score, Chi-Quadrat) hast du eingebaut, ich habe sie übernommen. 
Ich verstehe die Mathematik nicht im Detail, aber sie funktioniert: Sie verhindert, dass das System aus 5 Trades Schlüsse zieht.

Multi-Trade. 
max_positions in der Strategie-Konfiguration. 
Jede Position unabhängig mit eigenem SL, Trailing-Stop, Exit-Logik. 
In der Pipeline nicht vorgesehen.

Konfiguration statt Hardcoding. 
Alle Konstanten aus constants.py → YAML-Strategiedateien. 
Neue Strategie = neue YAML. 
Daemon lädt bei jedem Zyklus dynamisch neu, kein Restart nötig.


## Was noch nicht produktionsreif ist

- Flux Scoring: Design steht, Implementierung fehlt. 
Aktuell würde das System das erste aktive Enzym feuern - besser als Pipeline, aber der eigentliche Mehrwert fehlt.
- ISC-Verifikation: Operatoren definiert, Evaluator in substrate.py muss noch gebaut werden. 
Aktuell sind die ISC-Bedingungen Dokumentation, kein Code.
- Learning Engine: Tabellen definiert, aber tracker.py, analyzer.py, combination.py, trajectory.py existieren noch nicht. 
Dein ai_hindsight.py und signal_scorer.py müssen portiert werden.
- Trailing Stop, Position Sync, Daemon Loop: Design steht, Code fehlt.

Offene Fragen: Flux Scoring kostet Rechenzeit bei vielen Enzymen. 
Substrat wächst - wann Cleanup? 
Gleicher Flux Score bei zwei Enzymen - wer feuert zuerst? 
Fehlerbehandlung bei Enzym-Exceptions - aktuell undefiniert.


## Key Rotation (Nebenbei)

key_manager.py erlaubt mehrere API-Keys pro LLM-Provider. 
Bei 429/529 rotiert sie automatisch zum nächsten Key. 
War ein Snippet aus einem anderen Projekt. 
Ob es hier hilfreich ist, weiss ich noch nicht. Schadet nicht, kostet waren ein Punkt.

