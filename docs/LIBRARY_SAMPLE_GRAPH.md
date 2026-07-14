# Library Sample Graph — what the world already flipped from OUR shelves

A web-researched (WhoSampled snippets + Wikipedia + reputable music press) sample/
remix/cover map over the **top ~50 artists in the 14,336-track library**. Companion to
`LIBRARY_FLIP_MAP.md` (which inverts the answer-key corpus): this one goes the other
direction — take the artists we actually OWN and ask *"what famous flip, beat, remix,
or cover used them?"* Every such edge is a target the engine should be able to
rediscover on its own, using material already on disk.

**Method / honesty.** WhoSampled deep pages return HTTP 403 to automated fetch, so
every edge here is from indexed search snippets corroborated against Wikipedia / music
press. High-confidence = multi-source. Low = thin or nothing documented (kept in the
table so we don't re-research a dead end). Nothing fabricated; unverifiable rumors
(e.g. an of Montreal → Kanye sample) were dropped, not guessed.

## The payoff: ingredients we OWN that seeded famous flips

These are the strongest edges — a documented flip whose SOURCE side is in our crates.
This is the discovery punch-list: if the engine can't surface these from the raw
audio, that's the gap to close.

| Owned source | Flipped into | By | Kind | Conf |
|---|---|---|---|---|
| The Beatles (White Album) | The Grey Album (whole LP) | Danger Mouse | sample bed | high |
| The Beatles ('The End', 'When I'm 64'…) | Paul's Boutique — "The Sounds of Science" | Beastie Boys | sample | high |
| The Beatles ('Hey Jude') | Criminal Minded intro | Boogie Down Productions | sample | high |
| The White Stripes ('Seven Nation Army') | 50+ tracks + 80+ covers | (global) | riff/sample | high |
| Radiohead ('You and Whose Army?') | "Atonement" | The Roots | sample | high |
| Radiohead (Thom Yorke 'The Eraser') | "Us Placers" | Child Rebel Soldier (Kanye/Lupe/Pharrell) | sample | high |
| Radiohead ('Creep') | "Kreep" | Chino XL | sample | high |
| Coldplay ('Viva La Vida') | "Congratulations" | Drake | sample | high |
| Coldplay ('Viva La Vida') | "That Oprah" | Swizz Beatz | sample | high |
| Coldplay ('Clocks') | "Pump It Up" | Girl Talk | sample | high |
| Billie Holiday ('Strange Fruit') | "Strange Fruit" (1998) | Pete Rock | sample | high |
| Billie Holiday | "Cost Me a Lot" (Friday Night Lights) | J. Cole | sample | high |
| Gnarls Barkley ('Crazy') | "Crazy" flip | Lil Wayne | sample | high |
| Boards of Canada ('Blueberry') | "WeDontBelieveYou" | Bones/Xavier Wulf/Chris Travis | sample | high |
| Boards of Canada | "Ghost Girl" | Lil Peep | sample | high |
| Queens of the Stone Age ('No One Knows') | "Nobody's Listening (Green Lantern Rmx)" | Linkin Park | sample | high |
| Oasis ('Wonderwall') | "Jockin' Jay-Z" (prod. Kanye) | Jay-Z | sample | high |
| Oasis ('Wonderwall') | "Minute by Minute" | Girl Talk | sample | high |
| The Rolling Stones ('The Last Time' orch.) | "Bitter Sweet Symphony" | The Verve | sample | high |
| Hot Chip ('Ready for the Floor') | "Hands in the Air" (Feed the Animals) | Girl Talk | sample | med |
| Explosions in the Sky ('Your Hand in Mine') | "Lights Glow" | Rockie Fresh | sample | med |
| Wale ('Lotus Flower Bomb') | "Lotus Flower Bomb" (2023) | Lil Yachty | sample | med |
| Kings of Leon ('Sex on Fire') | "Use Somebody" | dvsn | sample | med |
| The Black Keys ('Howlin' for You') | "Black Skinhead" | Catfish & the Bottlemen | sample | med |
| Tom Waits ('Way Down in the Hole') | "Flippin' Off the Wall…" | 3rd Bass | sample | med |
| The Seatbelts ('Tank!') | "I I I I I I" | Sweet Valley | sample | med |

## Producers/samplers in the library (they flip OTHERS — source-side crates)

These artists are on our shelves but act mostly as *samplers*. They're worth mining
for technique/answer-keys, less as sampled sources.

- **Steinski** — foundational plunderphonics. "Lesson 1 (The Pay-Off Mix)" (over
  G.L.O.B.E. & Whiz Kid) cuts Bogart, Mae West, Herbie Hancock, Culture Club, Little
  Richard; Lessons 2 & 3; "The Motorcade Sped On" (JFK-broadcast collage). A whole
  cut-and-paste answer key.
- **Justice** — micro-samplers. "Genesis" (50 Cent, Prince, Queen, Slipknot, Cassius),
  "D.A.N.C.E." (Britney/Madonna, En Vogue, Chromeo), "Waters of Nazareth" (Booka).
- **Sage Francis** — indie-rap sampler: Oregon, Tangerine Dream, ODB, Michel Legrand,
  Kool & the Gang, East Flatbush Project across his catalog.
- **Moby** — 'Play' is a full-album sampler of Alan Lomax blues/gospel field recordings;
  famous more as sampler than as sampled.
- **Wale**, **Green Day**, **Gnarls Barkley**, **Justice** flagged role "both".

## Cover-magnets (rebuild-by-cover targets, not sample edges)

Widely covered but rarely sampled — useful for the cover/interpolation modes, not the
collage engine.

- **Tom Waits** — Rod Stewart "Downtown Train" (#3 Hot 100), Scarlett Johansson covers
  LP, the Wire "Way Down in the Hole" chain.
- **Gnarls Barkley 'Crazy'** — 60+ documented covers.
- **Elvis Presley** — JXL "A Little Less Conversation" remix is the marquee (estate
  restricts sampling).
- **Frightened Rabbit 'The Modern Leper'** — Biffy Clyro & Julien Baker (Tiny Changes
  tribute).
- **Saul Williams 'List of Demands'** — Robyn, The Kills.
- **QOTSA 'No One Knows'** — Mark Ronson horn cover, UNKLE reconstruction.
- **The Kooks 'Naive'** — Lily Allen. **Arctic Monkeys 'Do I Wanna Know?'** — 10+ covers.

## Thin / dead ends (documented so we don't re-research)

Little-to-nothing documented as sampled/remixed/covered: The Decemberists, Modest
Mouse, Drive-By Truckers, Weezer, Andrew Bird, The Front Bottoms, Sea Wolf, Los
Campesinos!, Beirut (one live cover), Cold War Kids (covers/one remix), Black Rebel
Motorcycle Club, of Montreal, Laura Marling, Two Gallants, Shearwater, Avett Brothers.
These are indie/folk acts rarely in the sample economy — consistent with the material
finding that the library's strength is the **troubadour** lane, not the chop lane.

## How this plugs into the engine

- **Recall check.** Each high-confidence edge above is a `reference_edge`-shaped truth
  we own the source for. Feeding these into the recall benchmark measures whether the
  engine rediscovers real flips from raw audio (not just from the curated corpus).
- **MusicBrainz batch (`earcrate/study/musicbrainz.py`).** These 50 are hand-verified
  seeds; the MB enrichment job scales the same question across all 14k tracks
  automatically (samples/remix/cover relationships), license-clean. This doc is the
  high-signal head; MB is the long tail.
- **Material reality.** The richest owned sources are the pop/rock pillars (Beatles,
  Radiohead, Coldplay, White Stripes, Stones, QOTSA) — the same pillars the Girl Talk
  answer key leans on. The chop/soul roster (Dilla lane) stays starved; no graph edge
  changes that, only records do.
