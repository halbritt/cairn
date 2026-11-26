# Cairn Token-Efficiency Audit Errata (`CAIRN-824` / `D3`)

**Date:** `2026-11-25`  
**Audit Snapshot Window:** `2026-09-07` to `2026-11-24T22:31:15Z` (M1–M2), `2026-11-24T23:44:43Z` (M3 offline), `2026-11-25T00:22:41Z` (M4 pilot rescore)  
**Scope:** Reconciles 13 verified discrepancies across the November 2026 token-efficiency audit deliverables (`01`–`07`) using existing snapshot records, live rollout state, and offline arithmetic without changing `available_tokens` API semantics.

---

## Verified Errata (Items 1–13)

1. **`available_tokens` is conservative UTF-8 bytes, not provider BPE tokens (`07 §1 #5`):**
   - `core/compiler.go:369,631` (`Tokenizer: "utf8-byte-upper-bound/1"`, `len(rendered) <= p.AvailableTokens`), `mcpapi/server.go:111-144`, and `integrations/lifecycle/inbox_recall.py:677-745` measure UTF-8 bytes rather than model BPE tokens.
   - API semantics remain unchanged (`utf8-byte-upper-bound/1`). Actual Claude/Gemini BPE tokenizer drift relative to the audit's `o200k_base` / `cl100k_base` proxies (expected `+-2-4%`) remains unmeasured (`What Was Not Measured`).

2. **Observed matched-session total (`93.09B`) exceeds whole-population estimate (`86.56B`) (`01 §1`, `02 §5.1`):**
   - Matched Cairn-enrolled sessions (`1,235 / 2,540`, `48.6%`) total `93,087,121,744` (`93.09B`: `67,788,089,980` `provider_reported_turn` across `424` sessions + `25,299,031,764` `provider_reported_session` across `811` sessions; `1,305` `unknown`).
   - This observed subset already exceeds the `86,561,210,050` (`86.56B`) pooled whole-population estimate (`88.10B` stratum-weighted) extrapolated from the `33`-session `15.19B` sample (`m2_opportunities.json`), reflecting non-uniform coverage skew in matched heavy sessions.

3. **Observation window mismatch (`Nov 18` vs `Nov 24`):**
   - `token_dashboard.daily_burn_days` froze at `2026-11-18T00:24:02.688Z` (`day_count 138 < 206` publish-gate rejection after Codex rollout JSONL purges on `2026-11-17/18`), whereas Cairn DB snapshots closed on `2026-11-24T22:00Z–22:31:15Z`.
   - Cross-table percentages comparing the `132.45B` host Cairn-era denominator (`2026-09-07..11-18`) against `2026-09-07..11-24` Cairn denominators (`93.09B` / `86.56B` / `38.92B`) span mismatched end dates.

4. **Context-replay median discrepancy (`58.0` vs `75.2` / `95.4` / `107–113` / `49–55`) (`01 §1`, `02 §5.2`, `03 §1`):**
   - The true `n = 8,423` wake-attempt median remaining API calls until compaction/end is **`58.0`** (`p95 = 292.0`), whereas **`75.2`** `= (95.4 + 55) / 2` is the 33-session Claude/Codex midpoint (`01 §1`, `02 §5.2`, `03 §1`).
   - Additionally, `01 §1` cites `95.4` Claude / `55` Codex medians while `02 §5.2` cites `107–113` Claude / `49–55` Codex medians while both cite `75.2`. Replay-amplified totals using `75.2x` (`1.17B` bodies, `1.25B` dup pulls, `523.3M` dup recall) scale higher than the `58.0x` `n = 8,423` median.

5. **Overlapping Rank 1–5 opportunity pools cannot be summed (`07` subtitle `"30B+"`):**
   - Opportunity categories across Ranks 1–5 overlap structurally (e.g., Rank 1 static prefix replay occurs inside Rank 2 wake turns and Rank 3/4 context pulls).
   - The `"30B+"` framing sums overlapping Rank 1 (`~18.31B` host prefix), Rank 2 (`20.16B` notice/response wake pool), Rank 3 (`1.25B+`), Rank 4 (`523.3M`), and Rank 5 (`259.8M`) pools and must not be added as independent additive savings.

6. **Notice/response wake pool size (`20.16B` vs `~17.14B–17.22B`) (`02 §5.2`, `03 §2`):**
   - The `20,158,795,446` (`20.16B`) pool across `10,392` notice/response wakes uses the `1,939,837.9` tok/wake weighted average from the `n = 1,891` Claude+Codex subset.
   - Using `02 §5.2`'s larger `n = 6,503` sample (`1,706` `response` at `1,888,777` tok/succ and `4,797` `notice` [`4,785` handled] at `1,567,735` tok/succ) implies `~1,651,957–1,652,113` tok/wake, or **`17.14B–17.22B` (~`17.17B`)** across `10,392` wakes.

7. **Failed-wake `1.19B` figure attribution (`03 §2` Rank 7 vs `07 §2` P4):**
   - The `1,193,000,307` (`1.19B`) wasted-wake figure came from `(151 processing_failed + 464 turn_ended) × 1,939,837.9` (`615` attempted wakes), **not** from the `451` `consumer_retired` failures (`total_attempts = 0` pre-dispatch/retired rejections).

8. **Pooled `32/34` (`94.1%`) vs `cd1` `13/14` (`92.9%`) M4 pass rate (`01 §1 #3`, `06 §1` vs `05 §3.1`):**
   - `32 / 34` (`94.1%`) is the pooled strict pass rate across **Arms B + C + D combined** (`B = 5/6 [83.3%]`, `C = 14/14 [100.0%]`, `D = 13/14 [92.9%]`), excluding `0/4` `qwen3.8-27b` budget failures. `cd1` (`D`) alone passed `13 / 14` (`92.9%`) on Claude+Codex.

9. **M4 `fresh_input` label (`343,131`) includes `cache_write` and `output_inclusive` (`01 §3`):**
   - In `m4_pilot_results.json`, the `343,131` figure is **`non-cached` (`output_inclusive` included)**: `72,100` `fresh_input` + `196,150` `cache_write` + `74,881` `output_inclusive` = `343,131` (plus `446,665` `cached_input` = `789,796` total tokens).

10. **Replay break-even (`58x` vs unamplified `1.8–2.4` / `2.46–3.26` messages) (`05 §4` row 3):**
    - `05 §4` row 3 multiplies per-message savings by `58` (`162 × 58 = 9,396`) while counting `cd1` skill overhead (`+307` for `888`-tok `skill_d_symbolic.md` or `+418` for `999`-tok `SKILL.md`) once.
    - Without replay amplification, persistent/cached-skill break-even is **`1.8–2.4` messages** (`888`-tok spec) or **`2.46–3.26` messages** (`999`-tok `SKILL.md`), and cold 1-shot break-even is `1.34` (`>= 2` exchanges) / `1.83` (`SKILL.md`).

11. **`cd1` header vs envelope `notice_action` for turn elimination (`01 §1` vs `07 §2`):**
    - `01 §1` states the `cd1` Line 1 header is required to unlock Cairn's `20.16B`-token turn-elimination lever, whereas `07 §2` schedules Cairn flags first (`P0`) ahead of `cd1` (`P3`).
    - Cairn's event envelope `action` (`none | review | decide`) together with `compact_inbox_version: 1`, `notice_digest_version: 1`, and `notice_auto_settle_version: 1` drives turn elimination independently of `cd1`.

12. **Syntax validity and `render_human()` do not establish semantic preservation (`CAIRN-819`–`CAIRN-821`):**
    - Observing `0` critical fabrications across `34` M4 Claude/Codex runs, `276/276` offline codec syntax checks, and `render_human()` prose output does not prove full semantic preservation; defects fixed in `CAIRN-819`–`CAIRN-821` (duplicate keys, `verified` without `chk`, path/handle canonicalization, and 5 compressed-example fidelity losses) remained outside those syntactic checks.

13. **`compact_inbox_version: 1` live status (`0/8` vs `4/8` post-`2026-11-24T06:22Z`):**
    - The audit's `0/8` global `compact_inbox_version: 1` claim and unqualified read-then-ack cost claim are wrong for post-`2026-11-24T06:22Z` sessions on `claude-one`, `claude-two`, `codex-one`, and `codex-two`.
    - Verified from `~/.local/share/cairn/go-rollout-ae498426/plans/stage_compat3.py` (mtime `2026-11-24T06:21:16Z`), staged generation configs (`06:21:33Z`), `watcher-bindings-v3/{claude-one,claude-two,codex-one,codex-two}.json` (`06:22:51Z`), `cairn-presence.service` restart (`06:23:00Z`), and `~/git/infra` commit `c4fcb330` (`CAIRN-301 w20`, operator `agent-2485`), `compact_inbox_version: 1` went live on those 4 bindings ~16 hours before the `2026-11-24T22:00Z–22:31Z` audit snapshots.
    - `m2_corpus_analysis.py:424` reported `0/8` because it read the retired `2026-11-06` `~/.local/share/cairn/coordination/bindings/*.json` directory instead of `go-rollout-ae498426/watcher-bindings-v3/`. For post-`06:22Z` sessions on those 4 bindings, payloads `<= 4,096` B were inlined without a separate read step, while `notice_digest_version: 1` and `notice_auto_settle_version: 1` remained off globally (`0/8` at snapshot time).

---

## `available_tokens` Contract Verification

- Verified unchanged across `core/compiler.go:369,631`, `mcpapi/server.go:111-144`, `integrations/lifecycle/inbox_recall.py:677-745`, `docs/mcp.md`, `docs/claude-lifecycle.md`, `docs/compact-inbox.md`, and `docs/search-room.md`: `available_tokens` measures conservative UTF-8 bytes (`utf8-byte-upper-bound/1`), not provider BPE tokens, and Claude/Gemini BPE calibration (`+-2-4%`) remains unmeasured.
