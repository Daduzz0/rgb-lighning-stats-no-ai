# RGB Lightning Stats — No AI

Scheletro per testare pagamenti BTC/RGB in regtest usando le API originali di
RGB Lightning Node e il routing predefinito del daemon. Wallet grafico opzionale:
[KaleidoSwap Desktop 0.6.0](https://github.com/kaleidoswap/desktop-app/releases/tag/v0.6.0),
in modalità **Node**, collegato a un nodo esistente.

Repository di destinazione:
[Daduzz0/rgb-lighning-stats-no-ai](https://github.com/Daduzz0/rgb-lighning-stats-no-ai).

Non vengono avviati Docker, Bitcoin Core, indexer o nodi Lightning. La preparazione
e i test automatici funzionano offline. Le connessioni REST avvengono solo con
`check-nodes` o `replay`; solo `replay` crea invoice e invia pagamenti.

## Subito, anche su un PC senza nodi

Serve soltanto Python 3.11 o successivo, senza pacchetti da installare:

```bash
python3 scripts/baseline.py validate
python3 scripts/baseline.py prepare
python3 -m unittest discover -s tests -v
```

`prepare` scrive in `generated/` il rapporto di validazione e quattro workload.
Non calcola percorsi, non simula pagamenti e non usa gli esiti storici come risultati.

I JSON originali rimangono inalterati:

| Dataset | BTC | RGB | Totale |
| --- | ---: | ---: | ---: |
| history-0020.json | 6 | 14 | 20 |
| history-0100.json | 34 | 66 | 100 |
| history-0500.json | 166 | 334 | 500 |
| history-2000.json | 666 | 1334 | 2000 |

Sono **prefissi cumulativi**: le transazioni uniche sono 2000, non 2620. La
topologia descrive 10 nodi e 15 canali. Manifest, checksum, provenienza sintetica,
conteggi e prefissi vengono controllati prima di ogni operazione.

## Sul PC del collega

Il collega prepara e sblocca i nodi regtest con gli strumenti che già utilizza.
Questo progetto non ne gestisce il ciclo di vita.

```bash
cp config.example.json config.local.json
```

In `config.local.json` compilare, per ciascun alias:

- `url`: URL della radice REST del **daemon RLN**, ad esempio
  `http://127.0.0.1:3001`; non il servizio personalizzato `/api`.
- `expectedPubkey`: pubkey reale del nodo, consigliata per verificare le identità.
- `bearerTokenEnv`: nome della variabile d'ambiente con il token Biscuit, se usato.
- `assets["rgb:synthetic-route-test"]`: contract id RGB realmente emesso sulla rete.

I `null` sono intenzionali: le porte 8801–8810 e le pubkey dei JSON **non sono una
configurazione live verificata**. L'asset sintetico non può essere inviato sulla rete.
La configurazione locale e gli eventuali segreti non vengono versionati.

```bash
# Sola lettura: verifica rete regtest, identità e accessibilità dei nodi coinvolti.
python3 scripts/baseline.py check-nodes --dataset 20

# Esecuzione esplicita: crea invoice nuove e invia i primi 20 pagamenti.
python3 scripts/baseline.py replay --dataset 20 --run-id baseline-20-01

# Primo controllo più piccolo, su una run distinta:
python3 scripts/baseline.py replay --dataset 20 --limit 2 --run-id smoke-02
```

`results/<run-id>/` contiene identità rilevate, stato dei canali prima e dopo,
journal persistente, tempi misurati ed esiti confermati da mittente e destinatario.
Un errore ambiguo o un pagamento ancora pendente interrompe la run; l'invio non
viene ritentato automaticamente. Il journal registra il payment hash prima
dell'invio. Una run esistente non viene sovrascritta: riconciliare prima il
pagamento tramite `/getpayment` e `/invoicestatus`.

## Cosa significa “senza AI” in questo benchmark

Il client invia `/sendpayment` con la sola invoice. Non invia `routing`, pesi,
scoring AI, percorsi storici, né selettori `yen`, `min_cost_flow` o `multi_objective`.
Il nodo sceglie percorsi, retry e comportamento MPP secondo la propria implementazione.
Non vengono importati moduli AIR-GB, QVAC o codice dal progetto personalizzato.

**Un client pulito non rimuove eventuali patch dai daemon.** Per una baseline
interamente upstream, anche i daemon devono essere originali e compatibili tra
loro. La sola API REST non certifica il codice eseguito dal nodo; il rapporto lo
indica come `not_attestable_via_rest`. Annotare versioni e commit nel laboratorio.

I budget fee e `max_paths` storici restano in `referenceOnly`, perché l'API standard
non li espone nello stesso modo delle aggiunte del progetto. Non sono vincoli
applicati: questa è una baseline del workload con i default del nodo, non un
confronto a vincoli identici. Commissioni effettive e hop sono `null` quando le API
originali non li restituiscono; non vengono copiati dai valori sintetici.

Gli scenari di `topology.json` con estrazione in linguaggio naturale, esclusioni,
selezione algoritmica e fault injection rimangono fixture: non vengono dichiarati
eseguiti da questo runner. Il supporto RGB MPP dipende dal daemon; il client non
replica gli split con pagamenti separati.

## Saldi iniziali e confronti ripetibili

I `history-*` partono da `initialChannelStates` con RGB ripartito a metà e BTC
secondo rapporti direzionali. `topology.json` descrive invece un setup con RGB
inizialmente tutto nella direzione del finanziatore. Sono due stati distinti.

Il collega deve predisporre canali, asset, fee e liquidità appropriati prima della
run. `generated/workload-*.json` conserva lo stato storico come riferimento con id
sintetici da associare a quelli reali. Il runner non ripristina saldi né scorer.
Prima di confrontare due esecuzioni, ripristinare le stesse condizioni e annotare
le differenze. L'indisponibilità e i fallimenti sintetici non vengono iniettati.

## Wallet grafico originale, opzionale

Su Linux x64, Python e GnuPG permettono di scaricare l'AppImage ufficiale, verificando
la firma del manifest con il fingerprint documentato dal progetto e il checksum:

```bash
python3 scripts/download-kaleidoswap.py
./avvia-wallet.sh
```

Nel setup scegliere **Node** e **Connect to an existing remote node**. Inserire
l'URL REST effettivo, selezionare regtest e usare le credenziali del nodo di test.
Il launcher apre l'app, senza avviare un daemon o un container. KaleidoMind non
serve. L'app upstream resta invariata; i JSON si usano con il runner, non si
importano come transazioni già effettuate nel wallet.

L'AppImage e le librerie runtime vengono scaricati sul PC del collega; non sono
inclusi nel repository. Per macOS/Windows usare il pacchetto della stessa release
dal sito del progetto; il runner Python non dipende dalla GUI.

## File da pubblicare

Codice, README, configurazione di esempio, `release-lock.json` e i sei JSON sintetici
possono essere versionati. `.gitignore` esclude `state/`, `runtime/`, `results/`,
`generated/`, `config.local.json`, cache e la copia locale dei sorgenti upstream.
Password, mnemoniche e stato di eventuali wallet locali restano esclusi.

Non sono stati eseguiti pagamenti live per validare questo scheletro. I test automatici
usano client REST finti e verificano integrità dei fixture, importazione degli importi,
assenza dei parametri personalizzati e gestione degli invii ambigui.
