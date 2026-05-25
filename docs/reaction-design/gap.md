# Gap Analysis — Was wirklich fehlt, was nur behauptet fehlt, und was wirklich kaputt ist

> Stand: Code-Review des gesamten v2-Codebase (core/, enzymes/, learning/, llm/)
> Ehrliche Bestandsaufnahme. Keine Schmeichelei, keine Panikmache.

---

## 1. Was der EXPLAINER fälschlicherweise als "nicht implementiert" bezeichnet

Diese Dinge **funktionieren bereits**. Der EXPLAINER lag falsch.

### 1a. ISC-Verifikation — FUNKTIONIERT ✅

**EXPLAINER sagt:** "Operatoren definiert, Evaluator in substrate.py muss noch gebaut werden"

**Realität:** `Substrate.verify_iscs()` und `_evaluate_isc()` sind **vollständig implementiert** in `core/substrate.py`. Alle 7 Operatoren (`any_score_gte`, `sl_set_or_no_trade`, `size_within_risk`, `count_lt`, `false_or_action_wait`, `all_field_gte`, `none_field_eq`) funktionieren. Der Daemon ruft `self.substrate.verify_iscs()` nach jedem Enzym-Schritt auf.

**Aber:** Die ISC-Definitionen sind hardcoded in `Substrate.DEFAULT_ISCS`. Die Doku sagt, sie sollen aus der Strategie-YAML kommen. Aktuell werden sie aus `config["validity"]` geladen, mit Fallback auf die Defaults. Das funktioniert, aber neue ISC-Bedingungen brauchen neuen Code in `_evaluate_isc()`.

### 1b. Learning Engine — FUNKTIONIERT ✅

**EXPLAINER sagt:** "tracker.py, analyzer.py, combination.py, trajectory.py existieren noch nicht"

**Realität:** Alle Module existieren und sind implementiert:
- `learning/analyzer.py`: Wilson Score, Verdict-Klassifikation, `update_signal_accuracy()`
- `learning/combination.py`: Chi-Quadrat, Paar-Extraktion, `update_combination_accuracy()`
- `learning/trajectory.py`: Trajektorie-Klassifikation, `update_trajectory_accuracy()`
- `learning/rulebook.py`: `generate_rulebook()`, `should_regenerate()`, `get_latest_rulebook()`
- `learning/weight_adjuster.py`: `compute_adjusted_weights()` mit Kontrarian-Unterstützung

### 1c. Trailing Stop — FUNKTIONIERT ✅

**EXPLAINER sagt:** "Design steht, Implementierung fehlt"

**Realität:** `_update_trailing_stop()` in `approve_exit.py` ist vollständig implementiert mit:
- Aktivierungsschwelle (ATR-basiert oder prozentual)
- Breakeven bei Aktivierung
- ATR-basierter Trailing-Abstand
- Richtungsbewusste Anpassung (Long: SL rückt nur hoch, Short: SL rückt nur runter)
- Peak-Price-Tracking

### 1d. Position Sync — FUNKTIONIERT ✅

**EXPLAINER sagt:** "Design steht, Implementierung fehlt"

**Realität:** `sync_positions.py` ist implementiert mit:
- Paper-Modus mit Fallback-Equity
- Live-Modus mit Exchange-Abfrage
- Reconciliation (entfernt Positionen die nicht mehr auf dem Exchange sind)
- Mark-Price-Updates für offene Positionen
- Konfigurierbare Sync-Häufigkeit (`sync.position_sync_every_n_cycles`)

### 1e. Daemon Loop — FUNKTIONIERT ✅

**EXPLAINER sagt:** "daemon.py existiert als Design, nicht als lauffähiger Code"

**Realität:** `core/daemon.py` ist vollständig implementiert mit:
- `initialize()`: DB, Config, Substrate, Scheduler, Signal-Handler
- `run_cycle()`: Hot-Reload, Reset, Enzyme-Auswahl, ISC-Verifikation, Persistenz
- `run()`: Endlosschleife mit Sleep
- Consecutive-fire Guard (3x gleiches Enzym = Loop erkannt)
- Cycle-Logging in DB
- Graceful Shutdown (SIGTERM/SIGINT)

---

## 2. Was tatsächlich fehlt oder kaputt ist

### 2a. Flux Scoring ist simpel, nicht gradient-basiert

**Problem:** Jedes Enzym hat eine `flux_score()`-Methode, aber die gibt **hardcodierte Prioritätswerte** zurück, keine berechneten Gradienten. Regulatoren: 10.0, Sensoren: 1.0-2.0, Transporter: 5.0, Isomerase: 0.0. Der Daemon wählt das Enzym mit dem höchsten Score — das ist Priority-basierte Auswahl, kein Gradient-Descent zum Attraktor.

**Warum das ein Problem ist:** Das Design-Dokument verspricht: "Simuliere die Transformation, messe den Gradienten vorher und nachher, feuere das Enzym mit dem höchsten positiven Flux Score." Das ist nicht implementiert. Aktuell feuert das System einfach das wichtigste Enzym, das aktiv werden kann. Das funktioniert, aber es ist kein Reaktionsnetzwerk im Sinne des Designs — es ist eine Prioritäts-Warteschlange.

**Lösungsansätze:**

| # | Ansatz | Beschreibung | Vorteil | Nachteil |
|---|--------|-------------|---------|----------|
| A | **Priority-basiert belassen** | Aktuelles Verhalten: Regulatoren zuerst, dann nach Priorität. Keine Simulation. | Einfach, schnell, deterministisch, funktioniert für die meisten Fälle. | Kein Gradient, keine Adaptivität. Enzyme feuern in fixer Reihenfolge. |
| B | **Heuristischer Flux Score** | Statt Simulation: Jedes Enzym berechnet seinen Score basierend auf dem aktuellen Substrat-Zustand (z.B. ScoreConfluence gibt höheren Score wenn Kandidaten über Threshold sind). | Adaptiv, keine Simulation nötig, relativ einfach zu implementieren. | Kein echter Gradient. Heuristiken müssen pro Enzym definiert werden. |
| C | **Vollständige Gradient-Simulation** | Wie im Design: Für jedes aktive Enzym, simuliere `substrate_after = enzyme.transform(copy(substrate))`, messe Distanz zum Attraktor vorher und nachher. | Echtes Reaktionsnetzwerk-Verhalten. Optimierte Enzym-Auswahl. | Aufwendig. Jedes Enzym braucht eine Deep-Copy-fähige transform(). Rechenzeit steigt linear mit Anzahl Enzyme. |

**Empfehlung:** **B (Heuristischer Flux Score)** — Die meisten Enzyme haben bereits sinnvolle `flux_score()`-Methoden (RequestExit gibt 5.0 bei SL-Breach, 3.0 bei TP, 1.0 bei Signal-Reversal). Das ist bereits ein heuristischer Flux Score, nur nicht konsistent. Wir formalisieren das: Jedes Enzym berechnet seinen Score basierend auf dem Substrat, statt hardcoded Werte zurückzugeben. Das gibt uns Adaptivität ohne den Aufwand einer vollständigen Simulation.

**Integration:** In `core/enzyme.py`: `flux_score()`-Methode bleibt, aber die Basisklasse gibt 0.0 zurück und jedes Enzym überschreibt sie mit Substrat-abhängiger Logik. Im Daemon: `run_cycle()` wählt weiterhin das Enzym mit dem höchsten Score. Keine Änderung am Daemon nötig, nur an den `flux_score()`-Methoden der einzelnen Enzyme.

---

### 2b. Learning Engine wird nie getriggert

**Problem:** Die Learning-Funktionen (`update_signal_accuracy()`, `update_combination_accuracy()`, `update_trajectory_accuracy()`) existieren und funktionieren, aber **kein Enzym ruft sie auf**. `RecordTradeOutcome` schreibt Trade-Einträge und -Exits in die DB, aber aktualisiert nicht die Accuracy-Tabellen. `UpdateRulebook` ruft `generate_rulebook()` auf, aber das liest nur bestehende Daten — es aktualisiert sie nicht vorher.

**Warum das ein Problem ist:** Ohne Accuracy-Updates hat das Learning-System keine Daten. Das Rulebook generiert leere oder veraltete Regeln. Die Gewichts-Anpassung hat keine Basis. Das System lernt nie.

**Lösungsansätze:**

| # | Ansatz | Beschreibung | Vorteil | Nachteil |
|---|--------|-------------|---------|----------|
| A | **Neues Enzym: UpdateLearning** | Ein Synthase-Enzym, das nach jedem Trade-Close `update_signal_accuracy()`, `update_combination_accuracy()`, `update_trajectory_accuracy()` und `compute_adjusted_weights()` aufruft. | Sauber getrennt. Enzym-Design bleibt konsistent. | Noch ein Enzym, das gepflegt werden muss. |
| B | **In RecordTradeOutcome integrieren** | Am Ende von `transform()` die Learning-Aufrufe hinzufügen. | Kein neues Enzym. Weniger Code. | RecordTradeOutcome wird komplexer. Verletzt Single Responsibility. |
| C | **Im Daemon nach Trade-Close aufrufen** | Nach `action == 'trade_closed'` im Daemon direkt die Learning-Funktionen aufrufen. | Einfach. Kein Enzym nötig. | Daemon wird zum God-Object. Schwer zu testen. |

**Empfehlung:** **A (Neues Enzym: UpdateLearning)** — Sauber, testbar, konsistent mit dem Reaktionsnetzwerk-Design. Aktiviert sich nach `action == 'trade_closed'`, feuert alle Accuracy-Updates, und schreibt die angepassten Gewichte zurück ins Substrat.

**Integration:** Neue Datei `enzymes/update_learning.py`. Aktivierung: `action == 'trade_closed'` oder alle N Trades (konfigurierbar). Schreibt: `substrate.learning.signal_accuracy`, `substrate.learning.suppressed_signals`, `substrate.learning.highlight_signals`, `substrate.learning.rulebook`. Registrierung in `main.py` wie alle anderen Enzyme.

---

### 2c. Trajektorie-Analyse ist eine Heuristik, keine echte Historie

**Problem:** `CollectPreTradeContext._estimate_trajectory()` baut eine **synthetische Historie** aus dem aktuellen Indikator-Zustand. Die Methode hat einen Kommentar: "Since we only have the current snapshot (not historical bar-by-bar indicator data), we estimate alignment trajectory from indicator strength and crossover signals." Das ist kein echtes Trajectory-Tracking.

**Warum das ein Problem ist:** ISC-007 prüft `coincidence_risk != "high"`. Wenn die Trajektorie falsch klassifiziert wird (weil die Heuristik ungenau ist), lässt das System Trades durch, die auf Zufall basieren, oder blockiert Trades, die auf echter gradueller Ausrichtung basieren.

**Lösungsansätze:**

| # | Ansatz | Beschreibung | Vorteil | Nachteil |
|---|--------|-------------|---------|----------|
| A | **Rolling-Window-Indikator-Historie** | CollectOHLCV speichert die letzten N Zyklen Indikator-Werte im Substrat. CollectPreTradeContext liest die echte Historie statt zu schätzen. | Echte Daten, keine Heuristik. ISC-007 wird zuverlässig. | Substrat wächst (N Zyklen × M Symbole × K Indikatoren). Mehr Speicher. |
| B | **Datenbank-Historie** | Indikator-Werte pro Zyklus in eine DB-Tabelle schreiben. CollectPreTradeContext liest daraus. | Substrat bleibt klein. Unbegrenzte Historie. | DB-Zugriff im Enzym (langsamer). Neues Schema nötig. |
| C | **Heuristik verbessern** | Bessere Heuristik: MACD-Crossover-Zeitpunkt, RSI-Verlauf über letzte 3 Zyklen, EMA-Cross-Datum. | Wenig Aufwand. Keine DB/Storage-Änderung. | Bleibt eine Schätzung. Keine echten Daten. |

**Empfehlung:** **A (Rolling-Window)** — Begrenzt auf die letzten 12-24 Zyklen (wie im Design-Dokument beschrieben: "3 Tage auf 4H"). Das sind ~50KB pro Symbol. Mit `substrate.market["indicator_history"]` als Dict. Das Substrat wächst kontrolliert, und `reset_cycle()` behält die Historie (nur die aktuellen Indikatoren werden zurückgesetzt).

**Integration:** In `CollectOHLCV.transform()`: Nachdem Indikatoren berechnet wurden, `substrate.market["indicator_history"]` aktualisieren (append current, trim to max 24 Einträge). In `CollectPreTradeContext.transform()`: Echte Historie aus `substrate.market["indicator_history"][symbol]` lesen statt `_estimate_trajectory()`. Die bestehende `_classify_trajectory()` in `learning/trajectory.py` kann direkt verwendet werden.

---

### 2d. Fehlende Enzyme (CollectLiquidations, CollectOnchain, CollectSentiment)

**Problem:** Die `enzyme-definitions.yaml` definiert CollectLiquidations, CollectOnchain und CollectSentiment. Es gibt keine entsprechenden Enzym-Dateien. Diese Module sind im Design-Dokument beschrieben, aber nicht implementiert.

**Warum das ein Problem ist:** Ohne diese Sensoren fehlen Liquidation-Walls, On-Chain-Daten (MVRV, Exchange-Flow) und Sentiment (Fear & Greed, Funding Rates). Das sind optionale Module (config-gesteuert), aber sie werden vom Design-Dokument versprochen.

**Lösungsansätze:**

| # | Ansatz | Beschreibung | Vorteil | Nachteil |
|---|--------|-------------|---------|----------|
| A | **Aus dem bestehenden Code portieren** | Franks `liquidation_client.py`, `onchain_client.py`, `coinalyze_client.py` existieren im alten Codebase. Als Enzyme wrappen. | Bewährter Code. Wenig Risiko. | Die alten Clients sind nicht unbedingt mit dem Substrat-Design kompatibel. |
| B | **Neu schreiben als einfache Sensoren** | Minimale Implementierung: Fetch + Substrat-Write. Keine komplexe Logik. | Sauber. Konsistent mit dem neuen Design. | Mehr Arbeit. Duplikation von bestehender Funktionalität. |
| C | **Vorerst weglassen** | Diese Module sind optional (`modules.liquidation: false` in der Strategie). In der ersten Version nicht implementieren. | Wenig Aufwand. Fokus auf Kern-Enzyme. | Design-Dokument ist nicht vollständig. |

**Empfehlung:** **C (Vorerst weglassen) + A (später portieren)** — Die Module sind optional und config-gesteuert. Wenn sie nicht aktiviert sind, feuern die Enzyme nicht. In der ersten Version reicht es, die Kern-Enzyme zum Laufen zu bringen. Wenn die Kern-Enzyme stabil sind, die optionalen Sensoren aus Franks Code portieren.

**Integration:** Wenn implementiert: Neue Dateien `enzymes/collect_liquidations.py`, `enzymes/collect_onchain.py`, `enzymes/collect_sentiment.py`. Jedes Enzym prüft `substrate.cfg("modules.X", False)` in `can_activate()`. Registrierung in `main.py`.

---

### 2e. Mark-Price-Updates nur alle N Zyklen

**Problem:** `SyncPositions` feuert nur alle `position_sync_every_n_cycles` (Default: 4) Zyklen. Das bedeutet, `mark_price` in offenen Positionen ist für 3 von 4 Zyklen veraltet. `RequestExit` und `ApproveExit` arbeiten mit diesen veralteten Preises — SL-Breach und Trailing-Stop basieren auf falschen Daten.

**Warum das ein Problem ist:** Bei 15-Minuten-Zyklen sind die Mark-Preise bis zu 45 Minuten alt. Bei 5-Minuten-Zyklen wären es 15 Minuten. In volatilem Markt kann das den Unterschied zwischen einem rechtzeitigen und einem verspäteten SL-Auslösen bedeuten.

**Lösungsansätze:**

| # | Ansatz | Beschreibung | Vorteil | Nachteil |
|---|--------|-------------|---------|----------|
| A | **Jeden Zyklus syncen** | `position_sync_every_n_cycles = 1`. Jeder Zyklus holt aktuelle Preise. | Immer aktuelle Preise. SL/TP sind präzise. | Mehr API-Aufrufe. Rate-Limit-Risiko. |
| B | **Separates Price-Update** | Neues Enzym `UpdateMarkPrices`, das nur Preise updated (leichtgewichtig, keine Full-Sync). Jeder Zyklus. | Aktuelle Preise ohne Full-Sync. Weniger API-Aufrufe als A. | Noch ein Enzym. |
| C | **WebSocket-Preise** | Statt REST: WebSocket-Verbindung für Echtzeit-Preise. Substrat wird asynchron geupdated. | Echtzeit. Kein Zyklus-Delay. | Komplexe Implementierung. Async-Architektur nötig. |

**Empfehlung:** **B (Separates Price-Update)** — Ein leichtgewichtiges Enzym, das nur `fetch_ticker()` für die Symbole der offenen Positionen aufruft. Das ist ein einziger API-Call pro Symbol, nicht ein Full-Position-Sync. `SyncPositions` bleibt für die seltene Full-Reconciliation (alle 4 Zyklen).

**Integration:** Neue Datei `enzymes/update_mark_prices.py`. Aktivierung: `portfolio.open_positions` nicht leer. Schreibt: `mark_price` in jeder Position. Priorität: hoch (3.0), damit es früh im Zyklus läuft. In `main.py` registrieren.

---

### 2f. Kein Error-Recovery im Daemon

**Problem:** Wenn ein Enzym in `transform()` eine Exception wirft, fängt der Daemon sie im `run_cycle()`-Loop ab, loggt sie, und bricht den Zyklus ab. Aber der Substrat kann in einem teilweise modifizierten Zustand sein — das Enzym hat vielleicht schon einige Felder geschrieben, bevor es gecrasht ist.

**Warum das ein Problem ist:** Ein teilweise geschriebenes Substrat kann zu inkonsistentem Zustand führen. Z.B. wenn `ExecuteTrade` die Position ins Portfolio schreibt, aber dann beim Setzen von `action = 'trade_open'` crasht, ist die Position drin aber `action` ist noch `wait`.

**Lösungsansätze:**

| # | Ansatz | Beschreibung | Vorteil | Nachteil |
|---|--------|-------------|---------|----------|
| A | **Deep-Copy vor jedem Enzym** | Vor jedem `transform()`: `substrate_copy = copy.deepcopy(substrate)`. Bei Exception: `substrate = substrate_copy`. | Garantiert konsistenten Zustand. Einfach zu implementieren. | Performance-Kosten durch Deep-Copy bei jedem Schritt. |
| B | **Transaktions-IDs im Substrat** | Jedes Enzym schreibt eine `transaction_id` am Ende von `transform()`. Der Daemon prüft, ob die ID gesetzt ist. Wenn nicht: Rollback. | Kein Deep-Copy nötig. Leichtgewichtig. | Enzyme müssen manuell die ID setzen. Vergessen = inkonsistenter Zustand. |
| C | **Enzyme sind atomar** | Design-Regel: Jedes Enzym liest alles, was es braucht, und schreibt erst am Ende alle Felder auf einmal. Bei Exception: Substrat ist unverändert, weil noch nichts geschrieben wurde. | Kein Performance-Overhead. Sauberstes Design. | Disziplin nötig. Schwer zu erzwingen. |

**Empfehlung:** **A (Deep-Copy)** — Einfach, korrekt, und der Performance-Hit ist bei unserem Substrat (ein paar KB) vernachlässigbar. Der Daemon macht bereits `self.substrate = best.transform(self.substrate)` — wir ändern das zu `substrate_copy = copy.deepcopy(self.substrate); self.substrate = best.transform(substrate_copy)`, und fangen die Exception ab, bevor wir `self.substrate` zuweisen.

**Integration:** In `core/daemon.py`, `run_cycle()`-Methode, im Enzym-Ausführungs-Block. Import `copy`. Ein Zeile Code-Änderung pro Enzym-Feuerung.

---

### 2g. Exchange-Client hat Initialisierungs-Bug

**Problem:** In `core/exchange.py`, `_get_data_exchange()` übergibt `kwargs` als positionales Argument an den CCXT-Konstruktor: `exchange_class(kwargs)`. Das sollte `exchange_class(kwargs)` sein... aber `kwargs` ist ein Dict, und CCXT erwartet Keyword-Argumente. Richtig wäre `exchange_class(**kwargs)` oder `exchange_class(config=kwargs)`.

**Warum das ein Problem ist:** In der aktuellen Form wirft CCXT wahrscheinlich einen TypeError, wenn versucht wird, Binance OHLCV-Daten zu laden. Der Fallback funktioniert möglicherweise auch nicht.

**Lösung:** In `_get_data_exchange()`: `self._data_exchange = exchange_class(kwargs)` → `self._data_exchange = exchange_class(**kwargs)`. Gleiches für `_get_trade_exchange()`.

**Integration:** Eine Zeile in `core/exchange.py`, zwei Stellen.

---

### 2h. Telegram-Credentials werden vom Substrat gestrippt

**Problem:** `SendTelegramLog` versucht, Telegram-Credentials aus `substrate.cfg("telegram.bot_token")` zu lesen. Aber der Daemon strippt `exchange` und `llm_keys` aus der Config, bevor sie ins Substrat kommt. Telegram-Credentials müssten in einem anderen Config-Bereich stehen, oder aus Environment-Variablen kommen.

**Warum das ein Problem ist:** Ohne Credentials kann kein Telegram-Log gesendet werden. Der Fallback auf Environment-Variablen funktioniert, aber die Config-Route ist kaputt.

**Lösung:** Environment-Variablen als primäre Quelle (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`). Substrat-Config als sekundäre Quelle. Das ist bereits implementiert, funktioniert aber nur wenn die Env-Vars gesetzt sind. Alternativ: Telegram-Section nicht strippen (nicht in `_SECRET_KEYS` aufnehmen).

**Integration:** In `core/daemon.py`: `_SECRET_KEYS` um `telegram` erweitern, ODER in `SendTelegramLog`: Env-Vars als einzige Quelle verwenden und den Substrat-Fallback entfernen.

---

### 2i. Kein strategie-UID-basiertes Learning

**Problem:** Das Design-Dokument beschreibt Strategy UIDs als primäre Schlüssel für alle Learning-Tabellen. Die DB hat die UID-Spalten (Migration 40-46). Aber `RecordTradeOutcome` schreibt `strategy_uid = substrate.strategy.get("uid", "legacy")`, und `ConfigLoader` generiert UIDs, ABER: Wenn die UID in der YAML leer ist, wird sie beim ersten Load generiert und zurückgeschrieben. Das funktioniert, aber wenn die YAML neu geladen wird und die UID fehlt (z.B. nach einem manuellen Edit), wird eine neue UID generiert und das Learning startet von null.

**Warum das ein Problem ist:** Learning-Daten gehen verloren, wenn die Strategie-YAML versehentlich ohne UID gespeichert wird.

**Lösung:** `ConfigLoader` prüft bereits auf leere UID und generiert eine neue. Das ist korrekt. Aber es gibt keine Warnung im Log, wenn eine bestehende UID ersetzt wird. Hinzufügen: Warning-Log wenn `uid` in YAML geändert wurde (alte UID vs. neue UID).

**Integration:** In `core/config_loader.py`: Beim Laden, alte UID aus DB holen und vergleichen. Warnung loggen wenn sie sich geändert hat.

---

## 3. Zusammenfassung: Prioritäten

| Priorität | Problem | Aufwand | Impact |
|-----------|---------|--------|--------|
| **P0** | Learning Engine wird nie getriggert | 1 Tag | System lernt nie. Kritisch. |
| **P0** | Exchange-Client Initialisierungs-Bug | 10 Min | Keine OHLCV-Daten. Kritisch. |
| **P1** | Mark-Price-Updates nur alle N Zyklen | 2 Std | SL/TP auf veralteten Preisen. Hoch. |
| **P1** | Error-Recovery (Deep-Copy) | 30 Min | Inkonsistenter Zustand bei Crash. Mittel. |
| **P1** | Flux Scoring ist hardcoded | 2 Std | Kein Gradient, aber System funktioniert. Mittel. |
| **P2** | Trajektorie ist Heuristik | 1 Tag | ISC-007 ungenau. Mittel. |
| **P2** | Fehlende optionale Enzyme | 2-3 Tage | Feature-Lücke. Niedrig (optional). |
| **P3** | Telegram-Credentials | 30 Min | Logging-Funktion. Niedrig. |
| **P3** | Strategy-UID-Warnung | 30 Min | Datenverlust-Risiko. Niedrig. |

**P0 = sofort beheben, P1 = diese Woche, P2 = nächster Sprint, P3 = wenn Zeit**