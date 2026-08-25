# Token numbering: the rules for adding a BASIC keyword

[日本語](token-numbering.ja.md)

## Why this file exists

A BASIC program is not stored as text. Every reserved word becomes **one byte**, and that
byte is what ends up in a `.sav`, on a cassette, and on a disk.

So a keyword's number is a **file format**, not an implementation detail. Give the same
number to different words in two builds and a program written on one runs as a *different
program* on the other - no error, no warning, just different behaviour. That is the exact
failure this repository exists to avoid, so the numbering is decided once, here, before
any keyword is added.

## The shape of it

The ROM holds two tables that have to stay in step.

**The reserved-word table** is `<token><word>` pairs, tokens from `$80` up, terminated by
`$FF`. V2 keeps it at `$C128-$C2C5`, V3 at `$CCAB-$CEBE`.

**The dispatch table** is a list of handler addresses, and **it is indexed by the token**:
`token - $80` for a command, `token - $CA` for a function.

```
  $80-$C9   commands    dispatch index = token - $80
  $CA-$EE   functions   dispatch index = token - $CA
  $EF-$FD   operators   (XOR OR AND NOT <> >= <= = > < + - MOD / *)
  $FF       the terminator of the reserved-word table
```

∴ **never change the number an existing word already has.** Every program ever saved
becomes a different program if you do.

Note *which* table forces this, because the two behave differently and it is easy to
misremember which: the reserved-word table carries an explicit token byte per entry, so
dropping a `<token><word>` pair into the middle of it does **not** renumber the entries
after it - they still say what they said. What it does instead is give the new pair a
number some other entry already claims, unless you renumber that one and everything after
it by hand. The **dispatch table** is the positional one: it is a bare list of addresses
indexed by `token - $80`, so an entry inserted in its middle moves every later handler one
slot out of step with the token that selects it, and each of those tokens then runs the
wrong routine.

To see what a particular dump uses and what is left:

```
$ ./tools/fb-basic-to-sav.py --tokens "Family BASIC V3 (Japan).nes"
```

Read the numbers from that, not from a table written down by hand. A hand-written copy is
how `SCR$` went missing from this repository's own V2 list and stayed missing.

## The rules

1. **Nintendo's numbers are frozen.** Nothing that exists in a stock ROM ever moves.

2. **Additions go in the range that is free in both V2.1A and V3**, so a program is
   portable across every build here (NROM 8KB, MMC5 16KB, FDS, and whatever comes next):

   | | reserved for this project | how many |
   |---|---|---|
   | commands | `$C1-$C9` | 9 |
   | functions | `$E7-$EE` | 8 |

   V2.1A has more room than that (`$B1-$C9` and `$E2-$EE`), but V3 already uses `$B1-$C0`
   for `TR` `FIND` `GAME` `BGTOOL` `AUTO` `DELETE` `RENUM` `FILTER` `CLICK` `SCREEN`
   `BACKUP` `ERROR` `RESUME` `BGPUT` `BGGET` `CAN`. Putting a new command at `$B1` on a V2
   build would make that program read as `TR` on a V3 build.

3. **Append in ascending order.** The dispatch table has to grow the same way, or the
   index stops matching.

4. **A new keyword must not begin with an existing one.** The tokenizer walks the table
   from the top and takes the first match, so `FDS` at `$B3` makes a later `FDSLOAD`
   unreachable - it matches `FDS` and leaves `LOAD` behind. Sub-commands have to be
   arguments (`FDS LOAD`), not separate keywords.

5. **Reusing a dead slot is allowed, and has to be written down below.** `GAME` is
   meaningless on a disk build - the built-in programs live above `$E000`, which is the
   BIOS there - so that slot can carry something else. The cost is that one number then
   means different things on different builds, which is exactly what rule 1 protects
   against, so it is a decision to record and not a habit.

6. **This file is the registry.** `tools/fb-basic-to-sav.py` carries the same numbers, and
   `--selftest` checks its list against a ROM for whichever version it is handed. Anything
   added here has to be added there.

## The registry

Nothing yet. Additions go here, one row each, with the build they apply to.

| Token | Word | Kind | Builds | What it does |
|---|---|---|---|---|
| | | | | |

### Slots reused from a stock keyword

| Token | Stock word | Now | Builds | Why the stock word is dead there |
|---|---|---|---|---|
| | | | | |

## What adding one actually involves

The reserved-word table is full where it sits, so a new word means moving it:

1. copy the table somewhere with room, add the entry, keep it in token order
2. repoint the code that reads it (V3: the four operands at `$93E7` `$93EB` `$94E1`
   `$94E5`)
3. **extend the dispatch table up to the new token's index, not by one entry.** The index
   is `token - $80` (commands) or `token - $CA` (functions), and after this step the table
   has to have `index + 1` entries (indices start at 0, so index 65 needs entries 0-65 -
   66 of them - to exist, not 65). If the table currently stops short of that, the entries
   in between have to exist first (pointing at whatever this ROM already does for an
   unassigned token) - otherwise the interpreter computes the right index and runs whatever
   happens to sit there instead, silently. **On V2.1A this gap already exists for both
   reserved ranges** (sizes read off `tools/fb-reach.py`'s `TABLE_RANGES`, not written down
   by hand): the command table has 49 entries (`$80-$B0`), so token `$C1` (index 65) needs
   16 placeholder entries (indices 49-64) plus the real one, 66 entries total; the function
   table has 24 entries (`$CA-$E1`), so token `$E7` (index 29) needs 5 placeholder entries
   (indices 24-28) plus the real one, 30 entries total. Check the same for whichever V3
   table this actually touches before assuming "one
   entry" there too - this file does not have V3's dispatch table size recorded
4. repoint the code that reads *that* (V3: `$8433` `$8438` `$9B39` `$9B3E`, and
   `$A4CF` `$A4D4` for functions)
5. add the word to `tools/fb-basic-to-sav.py` and to the registry above
6. `--selftest` will now disagree with the stock ROM, which is correct - it is checking a
   stock dump. Check the modified build with `--tokens` instead.

## One number that is more frozen than the rest

`$91` is `DATA`. `READ` finds its data by scanning the program **byte by byte for `$91`,
without skipping comments**, so any byte that happens to equal `$91` inside a `REM` is
read as a `DATA` statement. On a Japanese keyboard that byte is the small katakana `ェ`,
which is how the bug shows up in practice.

Adding keywords does not make this worse, and moving `$91` would make it very much worse -
every existing program's `DATA` would stop being found. Recorded here because "why can I
not renumber that one" is a question worth answering once.
