# Car Check — read this in the car

Everything you need to work out why the OBD adapter is not connecting, and what
to try when it isn't. No reference to this repo required.

**Safety:** the tool is read-only. It cannot transmit a write to the car even if
edited. It only asks the adapter what it is, and reads diagnostic data.

---

## 1. Before you drive out — 5 minutes, do it indoors

Plug the Veepeak into the car, **turn the ignition ON and leave it on**, then:

```
python main.py doctor --only-config
```

Prints what the tool is set to. You want:

```
adapter.type         elm327_serial
adapter.port         COM3
```

Also pair the adapter in Windows **before** you leave, then note the ports:
**Device Manager → Ports (COM & LPT)**. Write down every `COMx` you see.

> You need two COM ports for this to work later — see §5, gotcha 1.
> If only one appears, unpair the device, plug/unplug the adapter, and pair again.

---

## 2. In the car — the one command that matters

```
python main.py doctor
```

Takes under a minute. Eight stages, each `OK`, `WARN` or `FAIL`, then a
**Verdict**. Read the Verdict — it names the layer that stopped answering.

If it complains about the port being missing or locked:

```
python main.py doctor --all-ports      # tries every port until one opens
python main.py doctor --port COM7      # or name one specific port
```

---

## 3. What the stages mean

| Stage | Proves |
|---|---|
| 1 Configuration | settings are valid and the port is named |
| 2 Serial ports | which ports exist, and whether yours is among them |
| 3 Port open | the port is free and openable |
| 4 Adapter identity | an ELM327 answers and says what it is |
| 5 Protocol negotiation | at least one bus speed/type gets a reply |
| 6 OBD-II bus | the generic `0100` request is answered |
| 7 UDS by request ID | individual ECUs answer a VIN read |

**First `FAIL` is your answer.** Later stages get skipped — that's not extra
information, just avoiding the same timeout again.

---

## 4. Verdict → what to do

### "no ELM327 answered on it"
The port opened but nothing spoke.
1. You are probably on the **wrong COM port** — try the other one (§5).
2. Confirm it is actually paired in Windows Bluetooth settings.
3. Ignition ON, adapter pushed fully into the OBD socket.

### "does not identify as a known ELM327 family"
Cheap clone. It **warns, it does not fail.** If stages 5–7 pass, ignore it.

### "refused to change protocol / pinned"
The adapter answers `?` when told to switch protocol. It is stuck on whatever
protocol it powered up with, and the car is not on that one.
1. Unplug the adapter **completely** (both ends), wait 10s, plug back in.
2. Re-run. This resets the pinned protocol on most units.
3. Still refusing → firmware is ignoring `ATSP`, common on clones and some
   Bluetooth OBD firmwares.

### "the car never answered"
Adapter is fine, car is not talking.
1. **Ignition ON** and leave it on. The ID.3 gateway will not answer over OBD
   when asleep. This is the most common cause by a wide margin.
2. Reseat the OBD plug — on this car it is often only partly home.
3. Confirm the adapter is powered from the OBD socket, not USB alone.

### "OBD works but UDS does not"
Different addressing, not a dead link.
- Set the header to one ECU and read it by hand:
  ```
  ATSH7E5
  22F190
  ```
- If that returns a VIN but `doctor` did not, the adapter is resetting the
  header between requests.
- `NE` / *no response* is fine on an ECU — it means alive but not addressed.
  An **NRC** like `7F 22 31` means alive but VIN not supported.

### "port is locked by another process"
Only one process may hold the port. Close the dashboard, `main.py collect`, and
`tools/elm_console.py`. Bluetooth SPP has no sharing at all — not even a
second read-only opener.

### "COM3 is not present"
Not plugged in, not paired, or Windows assigned it a different number. Run
`--all-ports`, or check Device Manager again.

---

## 5. Three things that will waste your time if you don't know them

**1. Windows Bluetooth SPP creates TWO COM ports.**
One is *outgoing* (carries your bytes) and one is *incoming* (carries nothing).
The incoming port **opens cleanly and returns total silence** — it looks exactly
like a dead adapter. Stage 2 lists every port with its description; use the
one that says *outgoing*. This is the single most common failure here.

**2. A refused `ATSP` is not a sleeping car.**
If the tool says the adapter refused a protocol change, turning the ignition on
will not help. It needs a full unplug to reset. The tool separates these two
because otherwise they are indistinguishable.

**3. Ignition ON, and leave it ON.**
Not just ignition on — *stay* on. The gateway drops off the bus shortly after
lock, so a test that starts ten minutes late will report a sleeping car.

---

## 6. If it's still stuck — capture the raw bytes

This bypasses all the tooling and shows exactly what is arriving:

```
python tools/elm_console.py --no-init
```

Then type these one at a time, and write down every response:

```
ATZ
ATI
ATSP6
0100
```

Expect: `ELM327...`, a model string, `OK`, then a list of PIDs like
`41 00 BE 3F A8 13`.

| What you see | Means |
|---|---|
| nothing at all, ever | wrong port, unpaired, or no ignition power |
| characters but no `>` prompt | wrong baud rate, or a weak Bluetooth link |
| `?` to `ATSP6` | protocol is pinned — full unplug to reset |
| `NO DATA` to `0100` with `OK` to `ATSP6` | car is asleep; turn ignition on |
| `NO DATA` even after ignition on | OBD plug not seated, or wrong protocol |

---

## 7. Send the result back

Copy this into a message, fill in whatever you got, and send it:

```
Date / car state: ignition ON the whole time? yes / no
COM ports seen in Device Manager: COM__, COM__
Doctor verdict (copy all of §Verdict):

<paste>

Stage 2 port list:

<paste>

ELM console ATZ / ATI responses:

<paste>

What I changed between attempts:
```

---

## Reference

| Command | Does |
|---|---|
| `python main.py doctor` | full diagnosis against the configured adapter |
| `python main.py doctor --only-config` | print settings, touch no hardware |
| `python main.py doctor --all-ports` | try every port until one opens |
| `python main.py doctor --port COM7` | use a specific port |
| `python main.py doctor --timeout 10` | slower, for a flaky link |
| `python tools/elm_console.py --no-init` | raw interactive adapter access |

Exit code is `0` when nothing failed, `1` when a stage failed — useful for
scripts.

### Related

`python main.py doctor` diagnoses the *link*. `python main.py collect` is what
actually gathers readings, and only works once the doctor reports a connection.