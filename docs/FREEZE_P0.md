# P0 freeze (2026-10-28)

| Document | sha256 |
|---|---|
| docs/SPEC_drishti-swing-v1.md | e21dc0464df57704c0eada55f8e8ccefb5368d503d55cdd31b7fb6d8b4cc89d8 |
| docs/PREREG_M1.md (text) | cbe779641c9f46ea6644b13843d58a28f86ce07bc9f584f98a65272e4a4497f2 |

**Decisions recorded:**
- token: A (shared; Drishti reads Prajna's token, never logs in);
- blind on outcomes until the look;
- look = 40 sessions / 100 closed / 30 confirmed events, cap 120;
- survivorship: Upstox serves no delisted history, so every backtest is SURVIVORSHIP-BIASED, EXPLORATORY;
- costs: from the sample contract note.

**Backup:** the user declined a backup for P1 (2026-10-28). P1 only adds a new database `drishti` and a read-only role `drishti_ro`. Prajna's data is not modified.

**Changing a frozen file** = a new spec/model version with a new forward window.
