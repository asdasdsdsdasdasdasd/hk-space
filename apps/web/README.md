# apps/web —— 平面圖即時核查控制台

純內建的瀏覽器前端，接在现成嘅解算管線上面：
**揀户型 → 解算 → 睇圖 → 拖家具 → 即時重新核查**。
没有新依賴：Python 標準庫 `http.server` + 原生 canvas，没有打包器、没有前端框架。

## 點跑

```bash
cd hk-space
python3 -u apps/web/server.py --port 8791
# → http://127.0.0.1:8791
```

`--host` 預係 `127.0.0.1`。伺服器唔熱重載：改過 `server.py` 要重啟先會生效。
控制台唔會改任何 tracked 嘅 plan 檔案（佢用 `flat_from_dict(case["document"])` 讀户型，
唔會覆寫 `out/plan_hk_*.json`），所以可以随时跑，唔使驚污染 repo。

## 端點

| 方法 | 路徑 | 做乜 |
|---|---|---|
| GET | `/`、`/static/*` | 前端（`index.html` / `app.js` / `style.css`） |
| GET | `/api/health` | python 版本 + 可用 case 列表 |
| GET | `/api/cases` | case 名稱、標籤、面積、房間、家具件數 |
| GET | `/api/plan?case=onpak05` | 矢量化户型（房間 / 牆 / 門洞 / 服務點 / outline / meta） |
| POST | `/api/solve` `{case, solver, time_limit, parallel, variant}` | 開一個背景解算 job，即刻回 `{job}` |
| GET | `/api/jobs/<id>` | `state: queued\|running\|done\|error` + `result` / `error` |
| POST | `/api/verify` `{case, layout}` | 用你而家嘅摆法重新核查一次（唔重新解算）；拖曳、Auto-repair 後嘅即時重核同 Import 載入都行呢條路 |
| POST | `/api/repair` `{case, layout}` | 同上，但先對可挪嘅件跑一次 `try_nudge`，結果喺 `notes` |
| POST | `/api/chat` `{case, layout, message, history?}` | 一句話改設計：LLM 只揀動作，代碼執行，再交引擎重解 + 重核 |
| GET | `/api/file?p=...` | 只准 `data/floorplans`、`out`、`data/targets` 底下嘅檔案（底圖） |

解算 / verify 一次過俾齊 payload：`plan`、`bounds`、`layout`、`checks`（逐行）、
`scores`、`overlays`（曝光度格點 / 走廊 / 主動線）、`flags`（逐件家具嘅問題）、`metrics`、`elapsed_s`。

## 一條必須守住嘅規則：前端唔可以自帶一把尺

所有「合格 / 不合格」同「顯大分」都係引擎講嘅数，UI 只負責顯示：

- 核查逐行 = `bench_hk.check()`，連文案都係 `scripts/bench_hk.py` 嗰啲 tag。
  前端唔自己判「走廊夠唔夠闊」「傢俬有冇出界」。之前踩過嘅坑就係前端自造
  `min_corridor_width`，而 bench 嗰個 ruler 其實係 dead code —— 兩把尺唔同，睇落好似引擎有 bug。
- 顯大分 / detractor = `hkgeometry.spaciousness.spaciousness_report()`（`score`、`metrics`、
  `detractors`、`passes`），逐間房顯示。
- 疊圖 = `exposure_map`（熱力格點）、`corridor_reports`（走廊線 + penalty / blocked）、
  同 circulation 主動線（900 mm 閾值喺引擎嗰邊）。
- 逐件家具嘅紅旗 = `check_livability` 嘅 violations + `_unreachable_items` + solver 未放成，
  即 `flags_json()`，唔係前端另寫一套規則。

改規則請改引擎（`bench_hk.py` / `livability.py` / `spaciousness.py`），前端自然跟著變。

## 牆、門、窗係點畫

睇唔明嘅平面圖通常唔係數據錯，係畫得太輕。canvas 而家照住 `scripts/render_plan2d.py` 嗰套：

- **牆用真實厚度**（`thickness_mm × view.k`，最少 2 px），唔再係一條 1.5 px 嘅髮絲線；
  `load_bearing` 用 `#4a4a4a`、`partition` 用 `#8d8d8d`，同 renderer 嘅 `WALL_FILL` 一樣。
- **門窗係真嘅斷開**：牆先按 `openings` 切段再畫（同 `draw_wall_with_gaps()` 一樣），
  所以門位係一個窿，唔係一條紅線壓喺牆上面；窗係三條藍線（`#2a7ae2`）。
- **户型外殼**：`plan.outline`（HKHA shell）先鋪 `rgba(241,236,225,.9)`，再描 2.4 px 邊，
  唔會再見到幾間房浮喺空白上面。
- **地板按用途上色**（`living / bedroom / kitchen / bath`），同 `render_plan2d.py:49-50` 一樣。
- `plan` 而家有頂層 `walls` / `openings`（原本只係逐間房有），前端唔使逐間房搵。

## 疊圖（overlay）逐個講

全部由 `overlays_json()` 出，前端只負責上色，冇第二套判斷：

| overlay | 邊個計 | 睇到乜 |
|---|---|---|
| exposure heat | `exposure_map` | 逐格 0-100 嘅視線曝光，橙色越深越擋 |
| front clearance | `approach_zone` | 每件傢俬正前方要留嘅位（開櫃門／拉抽屜） |
| door swing zone | `door_approach_zones` | 門開盡掃過嘅扇形 |
| sightlines | `corridor_reports` | 走廊線；紅＝被擋、褐＝有 penalty |
| 900 mm artery（**預設關**） | `artery_clear(..., 900)` + `buffer(-450)` | 入口房間減去傢俬、再向內侵蝕 450 mm 之後淨低嘅淨空區；綠＝通、紅＝斷開 |
| labels / grid | 前端 | 房間名 + 面積；500 mm 網格 |

**900 mm artery 就係嗰笪淺綠色**：唔係地板顏色，係引擎算出嚟嘅主通道淨空區，
同 `bench_hk` 嘅 `0c 主通道连着每扇门且>=900mm` 用同一條尺。
呢層**預設關咗**（細單位上面太搶眼，而 0c 已經有講），想看就 tick `900mm artery`，
區中間會寫 `900 mm 主通道 · 淨空`（斷開就變紅字）。
侵蝕會留低冇面積嘅碎屑，前端會丟（`areaOf(pg) > 20000 mm²`）。
## 兩個鐘要分開睇

`elapsed_s` 嘅意思視乎邊個端點返嘅：

- `/api/jobs/<id>` 嘅 `elapsed_s` = **只有解算時間**（`solve_house` / `solve_progressive`）。
  `assemble()` 本身仲要跑核查 + 顯大分 + 疊圖（約 1 秒），呢啲唔計入「解算」。
- `/api/verify`、`/api/repair` 嘅 `elapsed_s` = **整個即時核查請求嘅真實牆鐘時間**
  （還原 payload + 核查 + 顯大分 + 疊圖）。呢啲端點冇解算，報 0 就係講大話。

狀態列因而顯示兩段：`解算 Xs`（解算嗰下）+ `即時核查 Ys`（拖完之後每次重核）。
目前一次即時核查約 0.9–1.1 s，前端 350 ms debounce，拖動期間唔會炸 request。

**標籤要老實**：`/api/repair` 同 import 都冇解算，所以淨係填 `即時核查`，
`解算` 嗰段必須清空（`state.solve_s = null`）。冇做過解算嘅請求永遠唔可以標籤做「解算」，
同理解算 job 嘅 `elapsed_s` 唔可以霸占咗「即時核查」嗰段。

## 拖曳同坐標

內部單位永远係**整數毫米**，`x,y` 係擺位嘅 min corner，`rot ∈ {0,90,180,270}`
（90/270 時 footprint 換 `w`/`d`）。圖面 +Y 向下，`flat_from_dict` 反翻成 +Y 向上再移到原點，
canvas 再翻返嚟：`Y(y) = view.oy - (y - bounds[1]) * view.k`。
拖曳 snap 50 mm，`Shift` + 方向鍵 = 250 mm。

移除一件家具：前端將佢喺房間 block 之間剪走，再將 inst 加入頂層 `layout.unplaced` ——
呢個先係伺服器睇到「移除」嘅方式（`placement_from_json` 根本唔理 `unplaced` flag）。
Items 分頁入面「未放成」嗰行撳一下可以放回原位。

## Export / Import 往返

Toolbar 有 `Export JSON` 同 `載入檔案`（`<input id="import" type="file">`）兩個掣，
用嚟將而家嘅摆法帶出去、喺別處改完再帶返入面**經引擎重新核查**：

- `#export` 將 `state.data.layout` 原樣寫成 `<case>_edited.json`（即 payload 入面 `layout` 嗰份）。
- `#import` 讀返呢個檔案，POST 去 `/api/verify`，再 `adopt()` —— 判分嘅依然係引擎，唔係前端自評。
  前端淨係收 `payload.layout || payload`，所以俾成份 export 檔案、定淨係 `layout` 嗰份都食得。
- `layout.rooms` 必須係**陣列**（`[{room, placements:[...]}]`，即 `layout_json()` 返嘅形狀）。
  俾咗舊式 dict 或者亂寫嘅檔案，前端即刻 reject，而畫面同狀態列**完全冇變動**（舊結果留喺度）：
  `載入失敗：呢個檔案冇 layout.rooms 陣列（請用 Export JSON 導出檔案先至可以載入）`。
  伺服器端 `house_from_payload()` 依然容得下舊 dict，呢個 gate 淨係前端嘅防呆。
- handler 先讀 `inp.files[0]` 到手，先至 `inp.value = ''`，所以**同一個檔案撳兩次都照跑**。
- 提示文案：開始 `載入中…`、成功 `已載入 <檔名> · 引擎重新核查完成`（2.5 s 後淡出）、
  失敗 `載入失敗：…`（留喺度等睇）。

一個實際往返（`onpak05`，`solver: cpsat` 解一次 → 原樣 export → 原樣 import，瀏覽器實測）：
解算嗰下 12/16、平均顯大 81.8；import 返入面重新核查仍然 12/16、81.8，
狀態列淨係多咗 `即時核查 0.92s`、`解算` 嗰段清空 —— 因為 import 嗰下冇解算。
（對照：同一個 `onpak05` 用預設 `auto` / progressive 俾嘅係 15/16 · 87.9，
所以 12/16 反映嘅係 solver 揀嘅摆法，唔係 import 損咗嘢。）

## 同設計師講（一句話改設計）

面板頂嘅 **「同設計師講」** 係一條 chat：你打一句（「客廳多一個書架」），
LLM 唔會自己擺位，佢只可以揀 `hkgeometry.llm_advisor.ACTIONS` 裏面嘅動作
（`add_item` / `define_custom` / `shrink_sku` / `move_item` / `drop_item` / `try_nudge` / `keep`），
之後全部由確定性代碼同引擎接手：

1. `parse_decisions()` 驗格式：唔識嘅動作、冇 target、`move_item` 冇 `to_room` 一律丟，並且回報原因；
2. `chat_gate()` 用 **`CATEGORY_ROOMS`** 擋品類 ↔ 房間（衣櫃只准睡房／儲物）—— 呢條係引擎規則，唔係 LLM 自己講；
3. `apply_decisions()` 執行（加件、換款、搬房、移除），每一步出 `[add] / [shrink] / [refuse]` note；
4. 真係有「重解通道」動作時，`solve_house()` / `solve_progressive()` 由頭解一次；
5. `assemble()` 再跑 `bench_hk.check()` —— 分數同合格永遠係引擎講嘅。

**加咗但擺唔落要當面講**：`house.unplaced` 有嗰件就出
`[engine] 摆唔落：#… —— 清單多咗，但解算之後冇位`。
bench 嗰張 17 件清單唔識新加嘅件，所以呢句係唯一會講出真相嘅地方。

三個鐘，各講各嘅：`解算` = solver 時間、`即時核查` = verify / repair / import 嘅牆鐘、
`傾偈` = LLM 嗰一次調用嘅時間。冇重解就**冇** `解算`（`chat.solved === false`）。

起點係任務書（`bench_hk.build_items`），所以 chat 重解會由引擎重新擺位；
你喺 UI 刪走嘅件唔會復活（`layout.unplaced` 會剔走），但拖曳位置會重新解過。

實測（同一個 console、真實 LLM 調用）：

| 你講 | LLM 揀 | 引擎結果 |
|---|---|---|
| 客廳多一個書架 | `add_item` shelf，BILLY 800×280×2020 | 放得落客廳，15/16 |
| 客廳加一個大衣櫃 | `define_custom` cabinet 1200×400×2400（衣櫃唔准入客廳） | 訂造櫃放得落，15/16 |
| 主人房加一個床頭櫃 | `add_item` side_table，GLADOM 450×450 | 放得落，15/16 · 顯大 87.2 |
| 主人房加第二個衣櫃 | `add_item` wardrobe，VUKU 750×500 | **擺唔落**：`[engine] 摆唔落` |

連唔到 LLM（冇 key / 冇 server）就係 `503` + 清楚嘅 `error`，前端出紅色 bubble，**唔會**假裝改到。
## 實測（同一個 console）

| case | 解算 | 即時核查 | 結果 |
|---|---|---|---|
| `onpak05`（安柏苑 05 室兩房） | 0.57 s | 約 1.0 s | 15/16，平均顯大 87.9；唯一 FAIL：`#toilet` 被 `#shower` 阻 |
| `stress_tiny` | 快 | 約 1 s | 12/12 |
| `stress_narrow` | 6.6–9.2 s | 約 1 s | 12/12，平均顯大 95.2 |

未改摆法直接 `/api/verify` 會還原返 15/16（round-trip 一致）；故意將 `#wardrobe_w` 壓喺
`#bed_w` 上面會即時跌到 14/16 並报 `FAIL 2 家具互不重叠 [('#bed_w','#wardrobe_w')]`；
`z` 或者 #undo 還原返 15/16。

## 冇做嘅嘢

上傳户型圖 / PDF（repo 冇 PyMuPDF，唔係呢一輪嘅範圍）、同埋真上線（呢個係內部驗證工具，唔係公網服務）。
chat 仲係「一句話 → 動作 → 重解」一輪：未做見到掉分就自己再改嘅多輪迴圈
（歷史會跟住 conversation 送返去，所以你可以補一句叫佢收手）。
`out/web_console_verify_clock.png`、`out/web_console_walls.png`（真牆厚 + 門窗）、
`out/web_console_chat.png`（一句話改設計）、`out/web_console_repair_notes.png` 係 console 嘅截圖
（`out/*` 被 gitignore，所以唔會睇到喺 git 入面）。
