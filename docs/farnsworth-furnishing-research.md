# Farnsworth House — the best-documented *real* furnishing scheme, for scoring a layout engine

**Target:** Mies van der Rohe, Edith Farnsworth House, Plano, Kendall County, Illinois (1949–51).
**Scheme selected:** **"Edith Farnsworth's Country House"** — the museum's presentation of the house
**as furnished by Dr. Edith Farnsworth c. 1955**, part of the exhibition *Edith Farnsworth Reconsidered*
(Farnsworth House / National Trust for Historic Preservation, opened 2020; curated by site director Scott Mehaffey).

**Why this scheme:** it is the only Farnsworth furnishing scheme published with **(a)** an explicit item-by-item
inventory labelled **A–Z**, **(b)** a **floor plan showing where each letter stood**, and **(c)** a stated date
(*c. 1955*) and stated basis (period photographs by Hedrich-Blessing, André Kertész and Werner Blaser).
It is therefore the only scheme a layout engine can be scored against point-by-point.

---

## 0. Sources actually retrieved (all public / open)

| # | Source | Retrieved how | What it gave |
|---|--------|---------------|--------------|
| S1 | *Furnishings — Edith Farnsworth Reconsidered*, exhibition slideshow PDF, 30 pp., 5.16.20 (edithfarnsworthhouse.org) | direct fetch = HTTP 403; **Wayback Machine copy**: web.archive.org/web/20241114064242id_/https://edithfarnsworthhouse.org/wp-content/uploads/Furniture-Slideshow-with-letters-5.16.20.pdf (3.61 MB) | the A–Z item descriptions; **page 4 = the labelled floor plan** |
| S2 | HABS **IL-1105**, measured drawings by Jenna-Celene Elizabeth Milnarik, Brad Dodge, Peter Summer 2009 (Library of Congress, record hhh.il0323) | tile.loc.gov/storage-services/master/pnp/habshaer/il/il0300/il0323/sheet/000NN a.tif (sheets 01–08) — note the loc.gov/resource/… viewer itself is Cloudflare-blocked | HO-1 floor plan, HO-2 core plan, elevations, wardrobe elevations; all dimensions below |
| S3 | HABS **IL-1105** written + descriptive data (NPS 1974 report by Susan S. Benjamin; **2009 addendum**) | tile.loc.gov/…/il0323/data/il0323data.pdf (55 pp.) | room-by-room description, the 1971–74 Palumbo refurnishing |
| S4 | Dezeen, *Farnsworth House installation replicates Edith Farnsworth's original decor*, 22 Jul 2020 | r.jina.ai proxy of dezeen.com | exhibition rationale, photograph sources, Palumbo/Mies/Lohan furnishing |
| S5 | National Trust for Historic Preservation, *The Barcelona Couch Represents a Specific Era of the Edith Farnsworth House* | r.jina.ai proxy of savingplaces.org | Palumbo bought the Barcelona Couch through Mies's office; 2003 National Trust acquisition |
| S6 | ArchDaily 944431, *Installation at the Farnsworth House Showcases Original Furniture of Edith Farnsworth*, 27 Jul 2020 | r.jina.ai proxy | exhibition framing |

**Method for positions:** S2 drawings were rasterised (7200×4800 px/sheet) and measured pixel-wise;
S1 plan page rendered at 400 dpi (5334×3000 px) and measured pixel-wise. Both were georeferenced to the
published envelope dimensions. All conversions are shown so they can be re-checked.

---

## 1. House coordinate system (what a placement engine should use)

Everything below is the glazed envelope only. Origin = **outer SW corner of the glazed box**.
**+X = east** (toward the sleeping / wardrobe end). **+Y = north** (away from the Fox River).
Units mm, Y-up, right-handed. (All imperial values are the values printed on the HABS sheets, in the original.)

### 1.1 Envelope and terraces

| Element | Dimensions | Position in house coordinates |
|---|---|---|
| **Glazed envelope** (the "one vast space") | **16 842 × 8 738** (55'-3" × 28'-8") | X 0…16 842, Y 0…8 738 |
| **Upper terrace / porch** (under the same roof, open to the west; screened by Farnsworth from 1953) | **6 790 × 8 738** (22'-3 3/8" × 28'-8") | X **−6 790**…0, Y 0…8 738 — i.e. the porch is at the **WEST** end |
| **Lower (travertine) terrace** | **16 842 × 6 810** (55'-3" × 22'-4") | NE corner at X **+3 410**, Y **−300**; spans X −13 430…+3 410, Y −7 110…−300 — set off to the **west and south** |
| Travertine floor grid | **610 (2'-0") N–S × 838 (2'-9") E–W** tiles, 1-1/4" thick | grid starts 710 mm south of the north glass inner face |
| Structural bays | 8 columns at **6 706 (22'-0")** o.c. | 4 central N/S glass panels 3 353 (11'-0") wide, flanked by 1 676 (5'-6") panels |
| Interior clear height | floor EL 2'-4" → ceiling EL 11'-9" = **2 870 (9'-5")** | 16'-1" grade → top of roof slab |
| West door (in the west glass wall) | opening ≈ **2 080** wide | centred at **Y ≈ 4 300**, i.e. 3 210…5 290 (HABS) / 3 400…5 390 (exhibition plan) |

### 1.2 The service core — **this is the one thing to get right**

Printed on HABS **HO-2 (Plan of Core)**: **24'-7 1/2" × 12'-6 1/4"** = **7 506 × 3 816 mm**.
Independently pixel-verified on HO-1 (3 988→5 220 px and 1 096→1 724 px at 164.15 px/m): 7 507 × 3 826 mm. **Agrees to 10 mm.**

| Face of the core | Distance to glass | House coordinate |
|---|---|---|
| North face (kitchen-counter back / north face of the storage bays) | **1 243 (≈4'-1")** south of the **outer** face of the north glass wall — ≈1 133 from its inner face | **Y = 7 495** |
| South face (living-area side; carries the fireplace) | **3 668 (≈12'-0")** north of the **outer** face of the south glass wall — ≈3 570 from its inner face | **Y = 3 668** |
| West face | 5 470 east of the west glass wall | **X = 5 470** |
| East face | 3 866 west of the east glass wall | **X = 12 976** |

Check: 1 243 + 3 816 + 3 668 = 8 727 ≈ 8 738 (28'-8"). **The core sits essentially in the middle of the
envelope, marginally EAST of centre** (core centre X = 9 223 = 0.548 of the envelope length; 0.68 of the
length of the whole building including the porch).

> **Correcting the brief.** The task statement says the core "is toward the west". The 2009 measured
> drawings say the opposite: measured from the west glass wall the core starts at +5 470 and ends at +12 976,
> so it is at the EAST-central part, next to the sleeping area and the wardrobe, with the
> dining/office zone (2 000–5 470) and the whole porch to its west. The **porch** is what is at the west end.
> Either way the brief's own numbers (55'-3" / 28'-8" / 24'-7 1/2" × ~12'-6") are confirmed.

Inside the core, north→south (HABS HO-2, right-hand dimension string): kitchen-counter/storage zone
**1 910 (6'-3 1/4")**, bathroom compartment depth **724 (2'-4 5/8")**, then the fireplace/cabinet band
**1 156 (3'-9 1/2")** to the south face. The core plan's north/south side dimensions read
**0'-3 1/4" | 4'-1 3/8" | 15'-11 5/8" | 4'-0 1/8" | 0'-3 1/8"** = 24'-9 1/4" overall E–W.

### 1.3 Georeference used for the furniture coordinates below

Exhibition plan (S1 p.4) at 400 dpi, page image 5334 × 3000 px:

~~~text
X_mm = (px - 2970) / 131.40 * 1000        Y_mm = (1608 - py) / 132.07 * 1000
~~~

(north glass outer face py = 454, south glass outer face py = 1608, west glass wall px = 2970,
east glass wall px = 5183, porch west edge px = 2075 — the drawn rectangle 3110 × 1154 px maps to
23 570 × 8 738 mm, ratio 2.692 vs the true 2.697.)

**Validation of the georeference:** the core's *west and east faces* come out at X = +5 495 and +13 135,
against +5 470 / +12 976 from the measured drawings — agreement within **160 mm**.
The core's *south face* comes out at Y = +4 051, against +3 668 — a **383 mm** disagreement, and the core's
north face at Y = +6 754 vs +7 495 — **740 mm**. So the museum's schematic draws the core ~360 mm shallower
and ~400 mm further south than the measured survey. **Treat every Y coordinate below as ±400 mm;
X coordinates are good to ±200 mm.**

---

## 2. The c. 1955 scheme — the labelled inventory

**Legend — Size:** [D] dimension printed in the exhibition document · [M] measured by me off the
exhibition plan (±50 mm) · [E] **ESTIMATE** (value not published; reasoning given).
**Conf:** H / M / L. **Orig:** whether the exhibition object is Farnsworth's actual piece or a stand-in
(the document marks each page "original" or "Contemporary" — this matters enormously for scoring).

| # | Item (as labelled by the museum) | Size (mm) | Size src | Position in house coordinates | Verbal location | Conf | Orig |
|---|---|---|---|---|---|---|---|
| **A** | **Stone lions** (pair of Chinese *shizi*, facing one another; male with cord + ball, female with pup; on carved stone blocks with additional lion protectors) | not published; **ESTIMATE ~500 W × 350 D × 900 H** each incl. plinth, from the drawn plan symbols | E | X ≈ **−12 100**, Y ≈ **−3 800** | on the **lower travertine terrace**, mid-depth, ≈1.3 m in from the terrace's west end, the pair facing each other across the walk | M / size L | **stand-in** — exhibition pair c.1800 N. China limestone; Farnsworth's own were **c.1700 marble**, later given to the Art Institute of Chicago and on extended loan to the University of Chicago (Cochrane-Woods Gallery) |
| **B** | **Upper terrace chairs** | not published; **ESTIMATE 570 W × 550 D × 750 H** (Bertoia 420 side-chair class) | E | X ≈ **−5 650**, Y ≈ **+4 260** | on the **upper terrace / porch**, mid-depth, ≈1.1 m in from the porch's west edge | position L / size L | **stand-in** — 8 Bertoia 420 chairs (4 c.2003 + 4 c.2015), donated by Knoll. *Farnsworth never owned similar chairs*; her originals were unidentified lightweight pieces she carried outside in good weather |
| **C** | **Rug — entry area** | **2 754 × 1 795** | M | X **1 286…4 040**, Y **3 271…5 066** (centre 2 663 / 4 169) | just inside the **west door**, in the entry zone in front of the door, running E–W | M | **stand-in** — contemporary Moroccan wool. Hers was a more traditional **"diamond"** pattern, same deep-pile type |
| **D** | **Chaise lounge chairs** — hers were a **Bruno Mathsson Model 36 chaise** plus a **Vilstol easy chair with leg rest** (the Vilstol is no longer made) | not published; **ESTIMATE chaise ≈ 1 700 L × 600 W × 620 H** | E | X ≈ **1 424**, Y ≈ **2 240** | the **south-west corner of the glazed space**, close to the south glass, ~1.4 m east of the west glass wall — chosen so she could slide them onto the screened terrace in warm weather | M | **stand-in** (Mathsson, donated by Bruno Mathsson SE). Hers were **laminated beech with woven hemp straps**; the exhibition chairs are birch with synthetic straps |
| **E** | **Occasional table / stool** — **Artek Stool 60 / E60** (Aalto, 1933) | **Ø380 × H440** (the standard, continuously-made Stool 60; **not** re-verified against a retrieved Artek spec sheet) | E | X ≈ **2 100**, Y ≈ **1 290** | against the south side, west of the daybed, beside the chaise group | M | **stand-in**. *Period photographs do not show this table in the house*; included because Baldwin Kingrey (her probable main supplier) sold it widely |
| **F** | **Planters** — cedar / redwood planters used as cachepots, on galvanised metal caddies on wheels | not published; **ESTIMATE ≈ 500 × 500 × 450** each | E | X ≈ **2 190**, Y ≈ **580** | on the travertine against/near the **south glass**, immediately west of the daybed | M | **stand-in**. Documented for her: tropical plants, flowering shrubs, a dwarf conifer, red geraniums; the caddies were to stop fertilised runoff staining the travertine |
| **G** | **Violin & music stand** (folding Hamilton Classic stand) | not published | — | X ≈ **5 120**, Y ≈ **4 260** | mid-house, in the **north-central zone** just west of the core, near the north side | L (label only) | **stand-in** violin. She owned a **Guarneri**; the sheet music shown is Vitali's *Chaconne* |
| **H** | **Floor lamp** — Arredoluce 3-arm "Triennale" (Angelo Lelli, c.1947) | not published; **ESTIMATE Ø ≈ 900 arm span, H 1 800** | E | X ≈ **5 480**, Y ≈ **2 870** | just west of the core's west flank, towards the south side, by the daybed | L (label only) | **stand-in** (lamp made c.1970s). Hers had a **tripod base** and was prone to tipping — period photos show the arms bent; later options were quad and stone bases |
| **I** | **Daybed** — white-painted steel frame, vinyl-coated rope, upholstered mattress, c.1952, **designer unknown** (possibly **Harry Weese**; possibly sourced by Baldwin Kingrey) | **990 × 1 926** (drawn); long axis **N–S** | M | X **6 160…7 150**, Y **1 658…3 584** | free-standing in the open floor immediately **west of the core's west face**, long axis N–S, facing the south glass | M | **custom replication** for the exhibition (CADtacular drawings from a period photo; Economy Iron frame, Von Dreehle-Freerksen ropes, Walter E. Smithe mattress). "A similar daybed appeared in Farnsworth House photos c. 1952" |
| **J** | **Rug — living area** | **3 137 × 2 197** | M | X **7 725…10 862**, Y **968…3 165** (centre 9 294 / 2 067) | centred in the **living area**, the band between the core's south face and the south glass, roughly on the fireplace axis | M | **stand-in**. Hers was light-coloured wool **with no pattern**; a light tone kept the narrow living strip feeling open, and the hearth-less fireplace blew ash onto the floor |
| **K** | **Lounge chairs** — 2 × Jens Risom (maple, woven straps); among the **first pieces in the house** | not published; **ESTIMATE 660 W × 720 D × 760 H** | E | X ≈ **8 550**, Y ≈ **1 260** | living area, in front of the fireplace, south of the core | L (label only) | **stand-in** (Knoll). Hers were **slightly darker wood with leather straps**, bought c.1951–52, most likely from Baldwin Kingrey |
| **L** | **Cocktail table** — Florence Knoll, 1952, white-painted steel (technically a **"corner table"**) | not published; **ESTIMATE 900 × 900 × 400** | E | X ≈ **9 380**, Y ≈ **1 250** | living area, beside the Risom chairs, in front of the fireplace | L (label only) | **stand-in** (Knoll). Hers had a **glass top which she soon covered with a piece of stone**; bought 1952 with the dining table |
| **M** | **Slipper chairs** — 2 low bamboo/rattan-frame chairs with woven cotton straps, **designer unknown** | not published; **ESTIMATE 600 W × 650 D × 700 H** | E | two drawn chairs: **(a)** X 9 842…10 579, Y 3 241…3 968; **(b)** X 9 796…10 398, Y 1 037…1 749 | (a) hard against the **core's south face — i.e. in front of the fireplace**; (b) near the **south glass** in the living area | M | **custom replication** (Fong Brothers, Los Angeles). Two low bamboo slipper chairs among the first pieces she brought in; 1951 photos show them **in front of the fireplace**, and from 1953 (once the terrace was screened) they were apparently used outside |
| **N** | **Draperies** — full-height, on tracks that **bisect the house**; the **tracks and rollers are original**, the fabric is not | track positions **NOT DOCUMENTED** (I could not resolve the two tracks on the plan) | — | label at X ≈ **13 780**, Y ≈ **650**; the tracks run **N–S across the full 8 738 depth** | as late as 1949 Mies proposed **two internal drapery tracks** bisecting the house to give privacy around the sleeping areas (the dining area was then still imagined as guest quarters) | L for which walls were used; H for the drapes' existence | **not original fabric.** 1951 originals: **Watson & Boaler**, loosely-woven **Chinese shantung silk**. Replaced after the flood 3 years later by white-painted basswood roll-up blinds (**The Aeroshade Co.**). The current set is the **third**, made by **InDecor 1997**, replacing the 1972 InDecor set |
| **O** | **Writing desk** — **Franco Albini** (1928 design, introduced to the US by Knoll 1949): white-painted steel frame, **glass top**, white-painted drawer unit. Shown with a Gio Ponti **Superleggera** chair, an Olivetti **Lettera 22** typewriter (Nizzoli, c.1949) and a Lightolier **"Cricket"** lamp (Thurston, c.1955) | **616 × 1 166** (drawn); long axis **N–S** | M | X **16 080…16 690**, Y **787…1 948** | **against the east glass wall**, in the sleeping area, running N–S so she sat facing west into the room | M | **stand-in** (Knoll / Cassina / loans). Hers was in this position in 1950s photos; by the **1960s it had moved out onto the screened upper terrace** |
| **P** | **Wardrobe / Hi-Fi cabinet** — Office of **Mies van der Rohe**, detailed by architect **William Dunlap**; first built by **Karl Freund** c.1952 (blond finish). **Centre section held the hi-fi; clothes closets on the opposite side.** | **measured**: length **≈3 700**, depth **≈740**, height **1 829** (top of wardrobe EL 8'-4" above floor EL 2'-4" = 6'-0") | D (height + HABS sheet 07) / M (footprint) | **the two plans disagree by 90°.** HABS HO-1 (measured) draws it **N–S**: X **13 890…14 630**, Y **640…4 340**. The exhibition plan draws it **E–W**: X **12 575…16 188**, Y **2 163…2 822** (3 613 × 659 drawn). HABS sheet 07's *North Elevation (South Similar)* is a **narrow** box while the *East Elevation* is **long**, which supports the **N–S** orientation of HO-1 | D / M | **not original** — a **1997 reconstruction by Parenti & Raffaelli Ltd** after the 1996–97 floods, now on display in the gallery building. All wood surfaces were stained brown under Palumbo |
| **Q** | **Carved wood object** — c.1870 Chinese camphor-wood architectural fragment (deer eating fruit trees) on a pedestal | not published; **ESTIMATE object 500 × 250 × 700 on a Ø400 × 900 pedestal** | E | X ≈ **14 260**, Y ≈ **3 240** | **at the end of the wardrobe**; the museum records a second such element between the wardrobe doors | L (label only) | **stand-in** (Pagoda Red). Her Asian antiquities were largely donated to the Art Institute of Chicago |
| **R** | **Rug — sleeping area** | **2 671 × 1 538** | M | X **13 200…15 870**, Y **4 152…5 690** | sleeping area, immediately **south of the bed**, laid on the open floor | M | **stand-in** (Moroccan wool). Hers had a **diamond pattern and was lighter in colour** |
| **S** | **Bed** — no headboard, no footboard | **1 910 × 1 401** (drawn); long axis **E–W** | M | X **13 130…15 040**, Y **5 660…7 062** | sleeping area, **immediately east of the core's east face**, head at the west (abutting the core), foot toward the east glass | **L** — the museum states flatly "There are very few known photographs of Dr. Farnsworth's private sleeping area"; one such photo (post-1954-flood, veneer drying) may show the bed pulled away from the wall | **stand-in** bedding (Lands End linen/matelassé; Room & Board cushions) |
| **T** | **Floor lamp** — Arredoluce 1-arm (Angelo Lelli, c.1947) | not published; **ESTIMATE Ø ≈ 400 base, H 1 700** | E | X ≈ **13 480**, Y ≈ **8 000** | between the bed's head and the **north glass wall** | L (label only) | **stand-in** (lamp made c.1970s). Hers had **a white shade and a white pole — a unique combination, possibly custom-ordered**. A 1-arm Arredoluce was also used out on the screened terrace in summer |
| **U** | **Bedside table** — plain pedestal-form occasional table in polished metal; the exhibition pairs a cast-aluminium/chrome table with a **Western Electric model 500 telephone** and one of her books | **464 × 462** (drawn table) | M | X **13 196…13 660**, Y **6 673…7 135** | at the head of the bed, between the bed and the **north glass wall** | M | table **stand-in** (Safavieh). **The telephone is original to the house** and may be hers or Palumbo's. Hers was a pedestal table with a **tripod base**, used in various places around the house |
| **V** | **Folding chair** — Gae Aulenti **"April 2200"** for **Zanotta** (1964) | **457 × 545** (drawn) | M | X **16 080…16 540**, Y **7 890…8 430** | the **north-east corner** of the glazed space, against the north glass by the east wall | M | **stand-in** (Zanotta). She owned **a pair of similar metal folding chairs of unknown origin** — occasionally on the screened terrace, but most often photographed in the **sleeping area** |
| **W** | **Kitchen counter** + accessories (Revere Ware chromed stainless "whistling tea kettle"; tray with Italian sweet vermouth and three rocks glasses) | counter depth **≈630** (measured on HO-1); length = the core's north side | M | along the **north side of the core**, X 5 470…12 976, Y ≈ **7 000…7 495**; the kitchen aisle between the counter's north edge and the north glass inner face is **≈1 130–1 270** clear | the **galley kitchen along the north glass**, with the core's storage bays and the two bathrooms immediately south of it | M | the counter **is original fabric**, but it **originally included a second oven and a dishwasher — both removed in Palumbo's 1970–72 renovation.** The kettle appears on her stove in the earliest photographs, so possibly a housewarming gift |
| **X** | **Dining table** — Florence Knoll, 1952, painted steel with **teak veneer top** | **1 979 × 940** (drawn) | M | X **1 651…3 630**, Y **6 634…7 574** | **dining area at the west end**, in the north half, long axis E–W, roughly 1.4 m in from the north glass | M | **stand-in** (Knoll). **Genuine purchase**: in 1952 she bought a dining table and matching cocktail table in white-painted steel to complement the house |
| **Y** | **End chairs** — 2 × Hans J. Wegner **Model JH 501 "Round Chair" ("The Chair")**, made by **Johannes Hansen**, teak with woven rattan seat | not published; **ESTIMATE 630 W × 530 D × 760 H** | E | the two ends of the table: X ≈ **1 110** and X ≈ **4 030**, both Y ≈ **7 000–7 440** | at the **east and west ends** of the dining table | M for position / L for size | **stand-in** (chair made c.1960s). The originals are the two Wegner chairs |
| **Z** | **Side chairs** — 4 chairs flanking the dining table. **The originals were of unknown origin** — "after careful study by several midcentury furniture experts in the US, Denmark and Italy, **no one could identify the original designer or maker**" | not published; **ESTIMATE 450 W × 480 D × 830 H** | E | along the table's two long sides; label at X ≈ **3 090**, Y ≈ **6 100** | flanking the dining table, two per long side | M for position / L for size | **stand-in** — Gio Ponti **Superleggera** (Cassina, from 1957). The originals were "similarly lightweight and open — but had a woven back as well" |

---

## 3. The other two schemes (kept strictly separate)

### 3a. Mies van der Rohe's own furnishing intent, 1951 — **documented as intent, not as an executed plan**

* Like many of his contemporaries **Mies regarded the building as a total work of art and expected to select and design all of its contents, furniture included** (Scott Mehaffey, quoted by the National Trust, S5). This is why the received history of the house is wrong: Farnsworth, not Mies, chose the furniture that actually went in.
* **His office did design one furniture piece for the house: the wardrobe / hi-fi cabinet** (started in the Office of Mies van der Rohe, postponed until after completion, detailed by William Dunlap, first built by Karl Freund c.1952) — see item **P**.
* The house was also **detailed with two internal drapery tracks** (1949 proposal) intended to give privacy to the sleeping zones — see item **N**.
* **No complete Mies furniture plan for the Farnsworth House was located in any publicly published source.** The Mies pieces habitually associated with the house — the **"Farnsworth"/"Barcelona" daybed-couch (Knoll)**, the **Brno chair**, the **Barcelona chair** — are documented at the house through later photographs and the Palumbo purchase, **not** through a 1951 Mies furnishing schedule. *Do not score an engine against a Mies scheme; there isn't one on the record.*
* Note the trap: the museum's c.1952 **daybed (item I) is a different object from Mies's Barcelona Couch.** The daybed is Farnsworth's, designer unknown; the Barcelona Couch is the Mies piece that only entered the house in the 1970s.

### 3b. Later curatorial refurnishing — the scheme usually on display

* **1972–1997, Lord (Peter) Palumbo.** HABS 1974: he bought the house in 1971 and "spent $20,000 to renovate the house and restore it to its original condition… the **entire house furnished with pieces either designed by Mies or specially designed for the house by Mr. Lohan**." He **bought a Barcelona Couch through Mies's own office** and completed the furnishing with Mies pieces and pieces by Mies's grandson, architect **Dirk Lohan**, who also worked on the restorations.
* **2003 –, National Trust for Historic Preservation / Landmarks Illinois.** Took over the site **including the Barcelona Couch and the other Palumbo-era furniture**. Those are "the furnishings typically on display in the space".
* **2020 –, "Edith Farnsworth Reconsidered / Edith Farnsworth's Country House."** The **c. 1955** presentation tabulated in §2 — the **first time in over 50 years** that the house was furnished with Farnsworth's decor, based on period photographs by **Hedrich-Blessing, André Kertész and Werner Blaser**.
* **NOT VERIFIED:** whether the Farnsworth c.1955 installation is still in place today, and its exact end date. The museum's *Furnishing Plan / interpretation* document, if it exists online, was not reachable in this session (the site's PDF endpoint returns HTTP 403 to non-browser clients; I retrieved its copy from the Wayback Machine).

---

## 4. Confidence summary and — explicitly — what is **NOT** documented

### 4.1 Confidence

| Grade | What it covers |
|---|---|
| **HIGH** | House envelope 16 842 × 8 738; porch 6 790 at the west; core 7 506 × 3 816 anchored 1 243 from the north glass and 3 668 from the south glass; floor grid; ceiling height 2 870; which end is which. **Item identity, designer and date** for all 26 letters A–Z. Which objects are genuine Farnsworth pieces vs. museum stand-ins. |
| **MEDIUM** | All X coordinates in §2 (±200 mm) and the *sizes* measured off the exhibition plan (±50 mm). Items C, D, E, F, I, J, R, S, U, V, X, and the Y/Z chair positions. |
| **LOW** | All Y coordinates (±400 mm, because the museum's plan draws the core ~400 mm too far south). Items shown on the plan **only as a floating letter** — B, G, H, K, L, N, Q, T. The bed's position and orientation. The slipper chairs (two drawn positions 2.5 m apart, only one of which can be "in front of the fireplace"). The wardrobe's orientation (two sources disagree by 90°). Every dimension marked [E]. |

### 4.2 NOT documented — do not fill these in silently

1. **No dimensioned furnishing schedule exists.** The exhibition PDF gives *identifications*, never dimensions. Every size in §2 is either printed in the HABS *architectural* drawings or measured by me off a raster of the exhibition plan.
2. **The exhibition plan carries no printed scale, north arrow, grid or dimensions.** My georeference is an inference from the envelope; the residual error is quantified in §1.3.
3. **The drapery track positions** — the two N–S tracks are not resolvable on the plan; only their *existence* is documented.
4. **The stone lions' size**, and the exact form of Farnsworth's own c.1700 marble pair — only that they went to the Art Institute of Chicago and are now on loan at the University of Chicago.
5. **The identity of the upper-terrace chairs** she actually owned ("lightweight pieces from the house she would move outside").
6. **The designer of the daybed** ("designer unknown"; the Harry Weese attribution is a *possibility* stated by the museum, not a fact).
7. **The origin of the four dining side chairs** — explicitly unidentified after study in three countries.
8. **The maker/designer of the slipper chairs.**
9. **The origin of her metal folding chairs** ("origin unknown").
10. **The bed's exact position and orientation**, and the bed's own dimensions as furniture (only the drawn rectangle).
11. **The exact Arredoluce lamp configurations** she owned (3-arm with tripod base; 1-arm, white shade + white pole — "may indicate it was custom-ordered").
12. **Anything about the second oven and dishwasher** beyond their presence in the original kitchen counter; positions and sizes not published.
13. **Anything on the screened upper terrace in the c.1955 state** except that the writing desk and a 1-arm Arredoluce lamp moved out there in the 1960s (i.e. *after* the 1955 date).
14. **Wall-, ceiling- and window-mounted items** (art, mirrors, the fire screen and iron log rack mentioned in passing).
15. **Whether the c.1955 installation is still up.**
16. **Manufacturer spec sheets.** Several vendor pages (Knoll, Cassina, Carl Hansen, Artek, Bruno Mathsson) either blocked the fetch or returned JS-only pages in this session, so no product-sheet dimensions are cited. Sizes marked [E] are my own typology-based estimates, not manufacturer figures.

---

## 5. Practical notes for scoring a layout engine against this scheme

* **Score position, not the object.** Only **A, D, I, K, L, M, O, S, W, X, Y, Z** are pieces she actually had in the same *model* (most of them re-manufactured). **B, C, E, F, G, H, P, Q, R, T, U, V** are stand-ins or reconstructions: use them as *placeholders for the correct typology and location*, never as evidence of a specific object's size.
* **The room is not a room.** The glazed envelope is one 16 842 × 8 738 space; the only internal obstacle is the 7 506 × 3 816 core (§1.2). A scoring harness needs exactly one axis-aligned obstacle plus the two glazed long walls, plus the porch as an optional external zone at X < 0.
* **The circulation constraint is the whole game.** The living band between the core's south face and the south glass is **3 668 deep**, and the kitchen galley between the core's north face and the north glass is **1 243 deep** — a 1 243 aisle is what the real house uses, so an engine that rejects anything under 1 200 will reject the house's own kitchen.
* **The known "oddities" of the real scheme are useful negatives.** Two slipper chairs 2.5 m apart, one hard against the fireplace wall and one at the south glass; a 3.7 m wardrobe; a writing desk *at the east glass wall* rather than in the "office area" shown on the 2009 measured plan. A good engine should *reproduce* some and *flag* others.

### Machine-readable landmarks (paste-ready, mm, origin = outer SW corner of the glazed envelope)

~~~text
envelope        X 0 .. 16842        Y 0 .. 8738
porch           X -6790 .. 0        Y 0 .. 8738      (upper terrace, west end)
lower_terrace   X -13430 .. 3410    Y -7110 .. -300  (travertine, west + south)
core            X 5470 .. 12976     Y 3668 .. 7484   (service core: kitchen, 2 baths, mech, fireplace)
aisle_south     Y 0 .. 3668         (living band, full length)
aisle_north     Y 7484 .. 8738      (kitchen galley, full length)
west_door       X = 0               Y 3210 .. 5290   (opening in the west glass wall, c. 2080 wide)
ceiling_height  2870 (floor EL 2'-4" to ceiling EL 11'-9")
floor_grid      610 (N-S) x 838 (E-W) travertine tiles, first gridline 710 south of the north glass
~~~

### Item centres (mm; from §2; ±200 X, ±400 Y)

~~~text
A lions         -12100, -3800     B terr.chairs    -5650,  4260     C entry rug      2663,  4169
D chaise          1424,  2240     E stool          2100,  1290     F planters       2190,   580
G violin          5120,  4260     H floor lamp     5480,  2870     I daybed         6655,  2621
J living rug      9294,  2067     K lounge chairs  8550,  1260     L cocktail tbl   9380,  1250
M slipper (a)    10211,  3605     M slipper (b)   10097,  1393     N draperies     13780,   650
O writing desk   16385,  1368     P wardrobe      14382,  2493     Q carved wood   14260,  3240
R sleeping rug   14535,  4921     S bed           14085,  6361     T floor lamp    13480,  8000
U bedside tbl    13428,  6904     V folding chair 16310,  8160     W kitchen ctr    9223,  7250
X dining table    2641,  7104     Y end chairs   1110 / 4030, 7220  Z side chairs    3090,  6100
# P conflict: exhibition plan draws P E-W at X 12575..16188, Y 2163..2822
#             HABS HO-1 draws P N-S at X 13890..14630, Y  640..4340   (centre 14260, 2490)
~~~

---

## 6. Retrieval notes for whoever picks this up

* loc.gov/resource/hhh.il0323.sheet/... and edithfarnsworthhouse.org/wp-content/uploads/... both return **HTTP 403** to curl (Cloudflare). Working routes:
  **https://tile.loc.gov/storage-services/master/pnp/habshaer/il/il0300/il0323/sheet/0000Na.tif** (N = 01…08; note the trailing **a**, and that **sheet 06 is the core plan, sheet 07 the wardrobe/bathrooms, sheet 03 the floor plan**), **https://tile.loc.gov/.../data/il0323data.pdf**, and **https://web.archive.org/web/20241114064242id_/https://edithfarnsworthhouse.org/wp-content/uploads/Furniture-Slideshow-with-letters-5.16.20.pdf**.
* The A–Z letter for each item can be extracted mechanically from the PDF: pdftotext -f N -l N on pages 5–30 returns the single capital letter that heads the lower-left corner of each page. Page 4 is the plan; the letters on it are **baked into the base image**, not vector text, so they must be read visually or by template matching (pages 5–30 *do* carry the letters as text).
* The 2009 measured drawings are **five HOUSE sheets (HO-1…HO-5) plus S-1/S-2 site sheets**; the TIFF numbering is not the sheet numbering.
* All working rasters from this session: /tmp/habs/s03.png (floor plan), /tmp/habs/s06.png (core), /tmp/habs/s07.png (wardrobe), /tmp/p4hi-04.png (exhibition plan, 400 dpi), /tmp/fw_slides.pdf (the exhibition PDF).
