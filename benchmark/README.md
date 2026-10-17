# Benchmark RGB standard contro AIR-GB / QVAC

Questo profilo esegue gli stessi **20 pagamenti sequenziali** della suite
`air-gb-lightning/wallet/infra/benchmark/scenarios.json`, sui 20 alias Alice–Tara.
È destinato al computer su cui verranno eseguiti i benchmark: la preparazione di
questo codice non richiede di avviare nodi, compilare Rust o inviare pagamenti.

## Input e stato iniziale

`scenarios.json`, `topology.json`, `common.mjs` e `compare.mjs` sono copie dei file
AIR-GB. `inputs-lock.json` ne fissa i digest SHA-256; il runner rifiuta modifiche
accidentali. La topologia originale nella root di questa repository e i dataset
`history-*.json` rimangono destinati al laboratorio storico.

Il setup dedicato crea:

- 20 nodi, con REST locale sulle porte 8801–8820 e P2P interno 9801–9820;
- i 15 canali del campo `channels`, con capacità, fee di apertura e quantità RGB
  dichiarate, push BTC e RGB iniziali a zero;
- 20 milioni di satoshi e quattro UTXO RGB da un milione di satoshi per nodo;
- l'asset TEST con emissione e allocazioni definite nella topologia.

Il grafo dichiarato collega Alice–Judy; Karl–Tara sono presenti ma isolati.
La stella opzionale `demoMesh` non è il grafo di questo profilo. Se la run AIR-GB
ha usato quella stella, lo stato iniziale differirà e il runner rifiuterà gli invii.

Ogni pagamento conserva mittente, destinatario, importi BTC/RGB, preferenze e
posizione nella sequenza della suite AI. Le preferenze rimangono nell'input per
l'audit; RGB standard usa il proprio router e non interpreta il testo tramite AI.

| Casi | Situazione esercitata |
| --- | --- |
| 01 | BTC diretto piccolo |
| 02–05 | RGB da 100/80 unità e pagamenti inversi; richiesta che può beneficiare di MPP |
| 06 | 101 unità RGB, oltre la capacità iniziale aggregata verso Judy |
| 07–08 | RGB da 60 unità e ritorno |
| 09–10 | Preferenza per fee basse e ritorno |
| 11–15 | Importi piccoli, nodi intermedi, rami con fee diverse, affidabilità |
| 16–17 | BTC multihop e importo superiore a un singolo canale |
| 18 | Preferenza privacy fra Heidi e Ivan |
| 19–20 | BTC/RGB fra nodi periferici isolati |

Le categorie descrivono gli stimoli: non sono esiti preimpostati. I saldi evolvono
e un fallimento precedente può cambiare la fattibilità di un caso successivo.
La suite confronta l'intera strategia sulla stessa sequenza, senza ripristinare i
saldi fra pagamenti.

## Routing standard e assenza di MPP

L'immagine dedicata usa il commit upstream fissato in `docker/upstream-lock.json`,
con router, scorer e retry upstream. L'invio REST contiene soltanto `invoice`:
non passa algoritmi, pesi o percorsi del fork AIR-GB.

Il daemon upstream impiega `RouteParametersConfig::default()` per BOLT11, che
non impone un singolo percorso. Per soddisfare il requisito **senza MPP**, la build
`docker/Dockerfile.benchmark` applica `docker/single_path.py`, che imposta
`with_max_path_count(1)` su quella chiamata. Rimane inoltre la strumentazione
`docker/instrument.py` per osservare fee e percorsi. Si tratta quindi di upstream
con strumentazione e limite a un percorso, non di un binario identico all'upstream.
Il runner usa esclusivamente invoice BOLT11 regtest.

Il runner verifica revision e label dell'immagine di tutti i container, registra
image ID e SHA-256 del binario effettivo e invalida il run se osserva più parti
regolate. Un retry su un altro percorso non viene contato come MPP riuscito:
i tentativi falliti sono registrati separatamente.

## Preparazione sul computer di esecuzione

Servono Linux, Docker Engine/Compose, Python 3.11+ e Node 24. Nessuna dipendenza
npm è necessaria. Prima eseguire e conservare la run AI, compresi `manifest.json`
e `initial-state.json`, secondo le istruzioni della repository AIR-GB.

Arrestare poi i servizi AIR-GB o gli altri laboratori che occupano le porte
8801–8820. La baseline usa volumi e rete Docker propri e genera nuove chiavi e
contract ID. Stessi nodi significa stessi alias e ruoli: il confronto normalizza
pubkey, identificativi dei canali e contract ID, senza copiare chiavi private.

Dalla root di questa repository:

```bash
make benchmark-up BENCHMARK_LABEL=machine-a
make benchmark-status
```

`benchmark-up` compila l'immagine, avvia e finanzia il laboratorio, apre i canali
e attende conferme e gossip. Genera `state/rgb-stats-benchmark/benchmark.json`
con i dati reali; non invia i 20 pagamenti del benchmark. Le credenziali restano
nella directory privata `state/`. `benchmark/config.example.json` documenta la
configurazione per un deployment preparato manualmente.

```bash
make benchmark-run \
  AI_RUN=/percorso/ai-01 \
  BENCHMARK_OUT=results/benchmark/stock-01

make benchmark-compare \
  AI_RUN=/percorso/ai-01 \
  BASELINE_RUN=results/benchmark/stock-01 \
  BENCHMARK_REPORT=results/benchmark/comparison-01.json

make benchmark-down  # arresta i servizi, conserva volumi e saldi
```

Il run richiede una directory di output nuova. Prima di creare la prima invoice,
verifica gli hash degli input, i timeout e lo stato iniziale osservato nella run AI:
alias, collegamenti, asset, capacità, liquidità BTC/RGB direzionale e usabilità.
Le differenze vengono scritte in `initial-state-comparison.json` e bloccano gli
invii. Anche riserve o commitment fee differenti possono impedire il confronto:
non si aggira il controllo modificando i risultati AI.

I saldi già consumati non vengono ripristinati da `benchmark-up`. Per una nuova
replica usare un progetto nuovo con `BENCHMARK_PROJECT=rgb-stats-benchmark-02`,
dopo aver fermato quello precedente per liberare le porte. Non riutilizzare una
rete già usata per altri workload. Nessun reset distruttivo è automatico.

## Misure e log

Per ogni transazione vengono salvati:

- input completo, hash del caso, payment hash, timestamp e stato dei due estremi;
- fee totale realmente pagata in msat, numero di percorsi regolati, hop totali e
  massimi, nodi/canali attraversati, importi e fee per percorso;
- tentativi di percorso falliti osservati e motivo del fallimento quando disponibile;
- tempo invio–conferma, tempo complessivo e durata della raccolta tramite `elapsedMs`;
- snapshot di tutti i nodi prima/dopo, hash dello stato e intervalli dei log;
- log stdout/stderr, log daemon/LDK ed eventi strutturati di ciascuno dei 20 nodi.

I dati di selezione indicano `stock_single_path`, modello e decisione AI sono
`null`, `selectionApplicable=false`. `selectionMs` misura solo la chiamata locale
che produce questi metadati e non rappresenta un tempo di inferenza.

Fee e hop provengono dagli eventi LDK, non dal budget richiesto o da un percorso
pianificato. Se un minimo HTLC causa un pagamento BTC superiore all'invoice,
`deliveredAmountMsat` e `overpaymentMsat` conservano l'eccedenza: le fee dei percorsi
più l'eccedenza devono coincidere con la fee effettiva di `PaymentSent`.
Misure non osservate rimangono `null` o incomplete; non diventano zero.

```text
RUN/
  manifest.json, summary.json       versioni, hash, provenienza, contatori
  suite.json, topology.json         input utilizzati
  initial-state.json               stato live iniziale
  initial-state-comparison.json     controllo contro AIR-GB
  results.jsonl, events.jsonl       risultati e avanzamento
  transactions/CASE/
    before.json, after.json         snapshot di tutti i nodi
    decision.json, intent.json      metadati e invoice/hash prima dell'invio
    result.json, status.jsonl       risultato e polling
  nodes/NODE/
    stdout.log, stderr.log          log Docker dal principio della run
    benchmark-events.jsonl          osservazioni normalizzate
    transactions.jsonl             ruolo del nodo e cursori per pagamento
    daemon/, ldk/                  copie dei log diagnostici
```

Non vengono esportati seed, password o database dei wallet. Gli artefatti possono
contenere invoice, pubkey e dettagli dei pagamenti regtest; le directory di output
sono private e rimangono fuori da Git.

Il runner invia ogni pagamento una sola volta. Dopo una risposta HTTP persa,
riconcilia tramite payment hash senza inviare di nuovo. Un fallimento confermato
permette di continuare; timeout o stati contraddittori fermano il run. SIGINT e
SIGTERM interrompono nuovi invii, senza annullare HTLC già in volo. Exit code 1
segnala problemi infrastrutturali, misure/log mancanti o una run incompleta;
un pagamento fallito con esito certo è un risultato valido del benchmark.

## Interpretare il confronto

Il comparatore condiviso produce JSON e CSV. Richiede 20 risultati unici, stessi
input, stessa topologia e stesso stato iniziale, senza esiti ambigui o MPP nella
baseline. Le percentuali di successo usano tutti i casi; fee, hop e latenze sono
calcolati sui successi corrispondenti e indicano il numero di campioni. I delta
sono **AI meno baseline**: valori negativi indicano fee, hop o tempi inferiori.
Controllare sempre `comparable`, `problems` e la completezza delle misure prima
di utilizzare i numeri del report.

`sameStateBeforePayment` evidenzia quando le due sequenze hanno già prodotto stati
diversi. Il confronto non dimostra che ogni singolo algoritmo sia migliore a
parità di stato intermedio e una singola run non prova significatività statistica.

Le API degli snapshot non espongono le policy di forwarding effettive: il setup
applica le fee dichiarate in apertura, ma le policy inverse upstream e quelle
pubblicate nella run AI vanno allineate sul computer di esecuzione. Il report
elenca questo limite fra gli `unverifiedControls`.

Per le latenze usare stesso hardware, carico, versione runtime e condizioni di
build; la stessa `label` è una dichiarazione, non una verifica automatica di questi
fattori. La build baseline attuale usa release opt-level 1 e 256 codegen units:
allineare i profili se si vuole attribuire una differenza di tempo al routing.
Il polling avviene ogni secondo. `endToEndMs` comprende snapshot e preparazione,
ma esclude la raccolta finale dei log; `elapsedMs` comprende anche quest'ultima.

## Controlli da eseguire sull'altro computer

```bash
make benchmark-contract-tests
```

Sono controlli locali con fixture e risposte simulate: non avviano Docker, non
compilano Rust e non inviano pagamenti reali. Coprono il formato condiviso, gli
eventi nativi, le eccedenze HTLC, i vincoli del confronto e l'assenza di retry di
invio. Sono forniti come codice; non costituiscono una validazione live del daemon.
