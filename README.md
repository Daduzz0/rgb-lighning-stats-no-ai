# RGB Lightning Stats — No AI

Laboratorio Docker regtest per misurare pagamenti BTC/RGB con il routing predefinito
[RGB Lightning Node](https://github.com/RGB-Tools/rgb-lightning-node).
Comprende dieci nodi, Bitcoin Core, electrs, proxy RGB, bootstrap dei quindici canali,
suite offline e suite di transazioni reali. Ogni tentativo aggiunge un record al log
persistente `results/transactions.jsonl` con importi, stato, fee, hop, percorsi e saldi.

Servono Python 3.11+, Docker Engine e il plugin Docker Compose. Il codice Python
usa soltanto la libreria standard; il laboratorio è pensato per Linux. La prima
compilazione Rust scarica le dipendenze e può richiedere diversi minuti e alcuni GB
di disco/RAM. La build usa un solo job per contenere il consumo di memoria.

## Confronto con AIR-GB / QVAC: 20 pagamenti, 20 nodi

Il profilo dedicato in [benchmark/README.md](benchmark/README.md) replica gli input
AIR-GB, prepara Alice–Tara e usa il routing upstream con un solo percorso per
pagamento. Raccoglie fee e hop dagli eventi LDK e confronta lo stato iniziale con
una run AI prima di inviare transazioni. Produce risultati JSON/CSV compatibili con
il comparatore AIR-GB. Richiede anche Node 24 sul computer di esecuzione.

Usare i comandi `make benchmark-*` descritti nella guida per questo confronto.
I comandi e i dataset storici descritti sotto continuano a usare il laboratorio
originario a dieci nodi e non sono gli input del benchmark AI a venti casi.

## Avvio e test

```bash
make validate prepare test
make up
make test-live RUN_ID=suite-01
```

`make up` compila una sola immagine RLN condivisa dai dieci container. Crea una rete
e volumi del progetto Compose `rgb-stats`, mina BTC regtest, inizializza e sblocca
wallet nuovi, finanzia ciascun nodo con 20 milioni di satoshi, crea UTXO RGB, emette
l'asset ROUTE, lo distribuisce e apre i quindici canali pubblici. Attende funding,
conferme, liquidità spendibile e gossip prima di dichiarare pronto il laboratorio.

Le REST API sono esposte solo su `127.0.0.1:8801`–`8810` (Alice–Judy). I servizi
Bitcoin, electrs, proxy e le porte P2P restano nella rete Docker. Non occupa le porte
host 18443, 50001 e 3000 di altri laboratori. I nodi di questo lab disabilitano
l'autenticazione REST; queste porte locali sono riservate ai test regtest.

La configurazione effettiva viene scritta in `state/rgb-stats/config.json`, con
pubkey e contract id reali. Password e mnemoniche sono conservate in
`state/rgb-stats/credentials.json`, accessibile soltanto al proprietario. Questi file,
i volumi e i risultati non vanno aggiunti a Git.

```bash
make status
make down                      # arresta; conserva wallet e volumi
make up                        # sblocca di nuovo; conserva i saldi correnti
make logs                      # log dei container
python3 scripts/lab.py mine --blocks 6
```

Un setup interrotto dopo il funding **non viene ripetuto automaticamente**: controllare
`state/rgb-stats/setup.json` e i log. Il checkpoint impedisce di duplicare emissioni,
funding e aperture. Non sono previsti reset distruttivi automatici. Per un laboratorio
completamente nuovo, dopo aver salvato i risultati, rimuovere esplicitamente solo i
volumi di questo progetto e archiviare la relativa cartella `state/`; non riutilizzare
configurazioni di identità appartenenti a volumi eliminati.

## Casi coperti

`make test` non avvia servizi e non invia pagamenti. Controlla integrità dei fixture,
normalizzazione e invio di **tutte le 2.000 richieste uniche**, BTC/RGB, transizioni
pending/success/failed, timeout, risposte malformate, identità/importi/asset errati,
assenza di retry degli invii ambigui, telemetria dei percorsi, log concorrenti,
scritture atomiche e isolamento Compose. Usa client simulati per verificare il runner;
i loro esiti non sono misure di rete.

`make test-live RUN_ID=suite-01` usa i container reali e scrive
`results/suite-01/suite.json`. Verifica:

- BTC e RGB diretti, inversi, multihop e multihop inversi;
- commissione zero nel pagamento diretto e misure di fee/hop complete nei successi;
- invoice duplicata, scaduta e malformata;
- importo BTC minimo del carrier RGB e rifiuto sotto il minimo;
- liquidità BTC e RGB esaurita;
- arresto reale di Bob e pagamento su un percorso alternativo;
- arresto reale di Heidi e Ivan, che taglia tutti gli ingressi verso Judy, e fallimento
  dei pagamenti BTC/RGB. I nodi fermati vengono riavviati e sbloccati in `finally`.

Per escludere intenzionalmente fault injection:

```bash
python3 scripts/transaction_suite.py --config state/rgb-stats/config.json \
  --run-id suite-ridotta-01 --no-faults
```

La suite modifica i saldi. Eseguirla su un laboratorio appena creato per confronti
ripetibili. Una run esistente non viene sovrascritta. Esiti ambigui interrompono la
suite e richiedono riconciliazione manuale tramite payment hash prima di nuovi invii.

## Eseguire i workload storici

```bash
make replay DATASET=20 RUN_ID=baseline-20-01
make replay DATASET=2000 RUN_ID=baseline-2000-01

# Prova più breve, con identificativo distinto:
python3 scripts/baseline.py replay --dataset 20 --limit 2 \
  --config state/rgb-stats/config.json --run-id smoke-02
```

| Dataset | BTC | RGB | Totale |
| --- | ---: | ---: | ---: |
| history-0020.json | 6 | 14 | 20 |
| history-0100.json | 34 | 66 | 100 |
| history-0500.json | 166 | 334 | 500 |
| history-2000.json | 666 | 1334 | 2000 |

Sono prefissi cumulativi: le transazioni uniche sono **2.000**, non 2.620. I sei JSON
originali sono invariati; manifest e checksum vengono verificati prima di ogni
operazione. `prepare` genera i quattro workload in `generated/` senza connessioni.
Il replay crea invoice nuove; i fallimenti storici non predeterminano quelli reali.

Il layout Docker predefinito `history` usa le quantità RGB divise a metà e i push BTC
dichiarati in `initialChannelStates`. I saldi BTC effettivi includono commitment fee
e riserve reali. Le fee inverse restano quelle predefinite upstream; gli scostamenti
sono annotati nel setup, con snapshot reali. `make up LAYOUT=topology` su volumi nuovi
usa invece la liquidità inizialmente tutta dal finanziatore; la suite live richiede
`history`. Il progetto non ripristina i saldi o lo scorer tra due run.

## Log aggiornato a ogni transazione

Il log principale è **`results/transactions.jsonl`**, cumulativo tra le run. Ogni riga
è un oggetto JSON indipendente e viene aggiunta con lock, flush e `fsync`. Una
transazione conclusa viene salvata prima dell'invio della successiva; un errore di
scrittura interrompe l'esecuzione. Il formato si legge con `tail -f` o qualsiasi
strumento JSONL:

```bash
tail -f results/transactions.jsonl

# Percorso cumulativo alternativo:
python3 scripts/baseline.py replay --dataset 20 \
  --config state/rgb-stats/config.json --run-id baseline-20-02 \
  --log results/misure-personali.jsonl
```

Campi principali:

| Campo | Contenuto |
| --- | --- |
| `runId`, `id`, `paymentHash` | Identità della run, richiesta e pagamento reale |
| `sender`, `recipient`, `amountMsat`, `assetId`, `assetAmount` | Nodi, importo BTC e asset RGB |
| `startedAt`, `finishedAt`, `durationMs` | Timestamp UTC e durata del flusso REST fino alla verifica finale |
| `status`, `senderVerified`, `recipientVerified`, `error` | Esito verificato e anomalie |
| `feeMsat` | Fee effettivamente pagata dall'evento LDK `PaymentSent`; include tutte le parti |
| `paths`, `pathCount` | Percorsi confermati da `PaymentPathSuccessful`, con alias e pubkey |
| `hopCount` | Somma degli hop dei percorsi riusciti; un pagamento diretto conta 1 |
| `paths[].hops` | SCID, pubkey, CLTV, importi BTC/RGB rilevati per ogni hop |
| `failedPaths` | Tentativi di percorso falliti, separati dalle parti riuscite |
| `telemetryComplete`, `telemetryError` | Disponibilità e coerenza delle misure rispetto all'importo della invoice |
| `observations`, `payment` | Stati osservati e campi REST del pagamento, esclusi i segreti |
| `channelsBefore`, `channelsAfter` | Snapshot BTC/RGB dei canali dei nodi osservati |
| `referenceOnly` | Dati sintetici originali, separati dalle misure |

`feeOrFinalAmountMsat` negli hop segue la semantica LDK: sull'ultimo hop è l'importo
consegnato, sugli hop precedenti è la fee di inoltro. Non va sommato come se tutti i
valori fossero fee. La fee totale viene presa dall'evento di pagamento concluso.
Quando fee o percorsi non sono disponibili rimangono `null`; non sono sostituiti da
valori storici. Le parti dei percorsi e le fee devono riconciliarsi con la invoice e con l'eventuale
eccedenza consegnata per i minimi HTLC (`overpaymentMsat`) per avere
`telemetryComplete=true`.

Ogni `results/<run-id>/` contiene inoltre:

- `journal.jsonl`: eventi persistenti, compresa la invoice e il payment hash **prima**
  di chiamare `sendpayment`, poi gli stati osservati e l'esito;
- `transactions.jsonl`: copia dei record completi della sola run;
- `run.json`: identità rilevate, provenienza del daemon e configurazione del benchmark;
- `summary.json` per i workload, oppure `suite.json` per la suite live, aggiornati
  atomicamente dopo ciascun caso;
- `channels-before.json` e `channels-after.json` per il replay.

Password, mnemonic, token, payment secret e preimage non vengono scritti nei log
strutturati. I log diagnostici upstream dei container possono contenere preimage;
trattarli come dati privati del laboratorio.

## Routing senza AI e provenienza del nodo

Il client invia `/sendpayment` con la sola invoice. Non invia `routing`, pesi,
percorsi precalcolati, selettori di algoritmo o scorer AI. La versione upstream è
fissata in `docker/upstream-lock.json`; Docker usa anche i submodule e `Cargo.lock`
dello stesso commit. `docker/instrument.py` aggiunge log JSON a tre handler di eventi,
senza cambiare routing, retry, scorer o API. Il daemon è quindi **upstream con
telemetria aggiunta**, non un binario upstream identico byte per byte.

La build di default è release con ottimizzazione 1 e 256 codegen units per contenere
la memoria; queste impostazioni sono annotate nel lock e nelle run. Non confrontare
i tempi con build di profilo diverso senza annotarlo.

Gli scenari originali di `topology.json` che richiedono estrazione AI, budget fee,
esclusioni o selezione `yen`/`min_cost_flow`/`multi_objective` non sono API del daemon
standard. La suite elenca **tutti** questi scenari in `originalTopologyScenarios` come
`reference_only`, con motivazione: non li dichiara superati mediante una simulazione
o sostituendo i risultati storici a quelli misurati. I test di indisponibilità live
agiscono invece sui container reali, con i default del nodo.

È ancora possibile usare nodi esterni: copiare `config.example.json` in
`config.local.json`, impostare gli URL REST delle radici RLN, le pubkey attese e
l'asset reale; usare `bearerTokenEnv` se servono token Biscuit. Lasciare `telemetry`
assente se i nodi non emettono gli eventi di questo progetto. In quel caso fee e hop
restano `null`. Verifica di sola lettura:

```bash
python3 scripts/baseline.py check-nodes --dataset 20 --config config.local.json
```

## Wallet grafico opzionale

Il download e launcher di KaleidoSwap Desktop 0.6.0 restano disponibili:

```bash
python3 scripts/download-kaleidoswap.py
./avvia-wallet.sh
```

La AppImage ufficiale viene verificata con firma e checksum di `release-lock.json`.
Nel setup scegliere **Node** e **Connect to an existing remote node**, regtest e,
per esempio, `http://127.0.0.1:8801`. Il launcher apre soltanto la GUI; il laboratorio
Docker viene gestito con `make up`. Il pin della GUI è indipendente dal pin del daemon
Docker: la compatibilità dell'intera GUI con questa revisione RLN non viene attestata
dalla suite di pagamento. I JSON non si importano come transazioni già effettuate.
