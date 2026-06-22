# PLAN — Pack generated facegen into BA2 archives

**Status:** BUILT 2026-06-22 (phases 1–5). Greenlit by Hugh; implemented per the
TDD phases below. esplib gained `ba2_hash`/`_hash_path`/`_parse_dds` + `Ba2Writer`
(GNRL + DX10), with hermetic hash-pin and reader-oracle round-trip tests. The
furrifier gained `pack.py` (`pack_facegen`) + a `--pack` CLI flag / "Pack BA2"
GUI checkbox, wired through `session.run(pack=)`. Verified end-to-end: a real
2-NPC `--pack` run emits `FO4FurryPatch - Main.ba2` (GNRL, valid NIFs) +
`- Textures.ba2` (DX10, valid 1024² BC7 DDS) and removes the loose trees.
**Remaining: phase 6 — in-game validation (Hugh)** is the only thing the self
round-trip can't prove (engine acceptance of the hash + single-chunk DX10).

## Goal

A full FO4 run emits thousands of tiny loose facegen files — one
`…\FaceGeom\<master>\<fid>.nif` and one
`…\FaceCustomization\<master>\<fid>_d.dds` per furrified NPC, spread across
several base-master subfolders. That's slow to install, clutters Data, and is
awkward for mod managers. Instead, optionally pack the run's facegen output into
two game-native archives named after the patch plugin so FO4 auto-loads them:

- `<patch-stem> - Main.ba2`   — GNRL archive holding the FaceGeom **.nif** files
- `<patch-stem> - Textures.ba2` — DX10 archive holding the FaceCustomization **.dds**

e.g. `FO4FurryPatch - Main.ba2` + `FO4FurryPatch - Textures.ba2`. The patch
plugin is in the load order, so FO4 auto-loads `<stem> - Main.ba2` /
`<stem> - Textures.ba2`. The patch's archives load after the masters' archives,
so our facegen overrides vanilla's — this is exactly how facegen/NPC-overhaul
mods ship vanilla-NPC faces.

Paths **inside** the archive keep the `…\FaceGeom\Fallout4.esm\<fid>.nif`
layout, so every base-master subfolder coexists in one archive and the engine
resolves each file by its in-archive path across the global archive VFS. (The
base-record-folder rule from `project_fo4_support.md` is unchanged — we're just
moving those same paths from loose into an archive.)

## Verified ground truth (spikes, 2026-06-19)

### BA2 container layout (from `esplib/src/esplib/ba2.py` reader, confirmed)

Header (little-endian):
```
char[4]  magic = "BTDX"
u32      version            # vanilla FO4 = 1 (confirm against a vanilla .ba2)
char[4]  type = "GNRL" | "DX10"
u32      file_count
u64      name_table_offset
```

**GNRL** file record (one per file, contiguous after the header):
```
u32 name_hash      # see hash below (file STEM)
char[4] ext        # first 4 bytes of extension, e.g. "nif\0", "dds\0"
u32 dir_hash       # see hash below (directory path)
u32 flags          # 0
u64 offset         # absolute offset of this file's data blob
u32 packed_size    # zlib size; 0 (or == unpacked) means stored uncompressed
u32 unpacked_size
u32 align          # 0 is fine
```
Then the file-data blobs at their offsets, then the name table.

**DX10** file record (24-byte tex header + N chunk records):
```
u32 name_hash
char[4] ext = "dds\0"
u32 dir_hash
u8  unk = 0
u8  chunk_count
u16 chunk_hdr_size = 24   # size of one chunk record (0x18); confirm vs vanilla
u16 height
u16 width
u8  num_mips
u8  dxgi_format          # e.g. 98 = BC7_UNORM (our diffuse), 83 = BC5 etc.
u8  is_cubemap = 0
u8  tile_mode = 0
```
Each chunk record (24 bytes):
```
u64 offset           # absolute offset of this chunk's data
u32 packed_size      # zlib; 0/==unpacked = stored
u32 unpacked_size
u16 start_mip
u16 end_mip
u32 align = 0
```
The DDS stored in a DX10 archive has **no on-disk DDS header** — only the raw
mip pyramid bytes, split across chunks (concatenated largest-mip-first on
extract). Our reader rebuilds the DDS header from the tex-header fields
(`_build_dx10_dds_header`); the **writer is the inverse**: parse the .dds we
wrote → tex-header fields + raw mip body.

**Name table** (at `name_table_offset`, entries in record order):
```
for each file:  u16 name_len; char[name_len] name   # cp1252, backslashes
```

### The file/dir hash (THE feasibility linchpin — VERIFIED)

NOT zlib's standard CRC32. It is a **raw table CRC32**, matched 60/60 on both
name and dir hashes of a vanilla GNRL archive (`Fallout4 - Interface.ba2`):

- polynomial: **0xEDB88320** (reflected — same table zlib uses)
- **init = 0, final-XOR = 0** (this is the difference from zlib's standard
  CRC32, which inits to 0xFFFFFFFF and XORs the result with 0xFFFFFFFF)
- input: the string **lowercased**, encoded **cp1252**, backslash separators

```python
def _make_table(poly=0xEDB88320):
    t = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = ((c >> 1) ^ poly) if (c & 1) else (c >> 1)
        t.append(c & 0xFFFFFFFF)
    return t

_T = _make_table()

def ba2_hash(s: str) -> int:          # raw CRC32, init 0, no final xor
    c = 0
    for b in s.lower().encode("cp1252", "replace"):
        c = ((c >> 8) ^ _T[(c ^ b) & 0xFF]) & 0xFFFFFFFF
    return c
```

Per file path `dir\stem.ext`:
- `name_hash = ba2_hash(stem)`   (no extension)
- `dir_hash  = ba2_hash(dir)`    (backslashes, no leading/trailing slash)
- `ext`      = first 4 bytes of the extension (e.g. `b"nif\0"`, `b"dds\0"`)

The engine resolves by this (dir_hash, name_hash, ext) triple, so getting it
right is mandatory — the name table alone is not enough.

### Current facegen output flow (integration points)

- `session.run(scheme_name, patch_name="FO4FurryPatch.esp", output_dir=…)`
  (`session.py:65`) → `out = output_dir or data` (`:131`) → creates the patch at
  `out/patch_name` (`:148`) → `patch.save()` (`:357`) → if facegen enabled,
  `build_facegen_for_patch(..., output_dir=str(out))` (`:371-378`).
- `build_facegen_for_patch` (`facegen/__init__.py:133`) writes loose files under
  `out_root`:
  - `meshes/Actors/Character/FaceGenData/FaceGeom/<plugin>/<fid>.nif`
  - `textures/Actors/Character/FaceCustomization/<plugin>/<fid>_d.dds`
    (only `_d.dds` per NPC — normal/spec are shared base-head maps since the
    2026-06-11 VRAM fix, so the Textures archive is all BC7 diffuse).
- CLI flags: `config.py` argparse (`--patch`, `-o/--output`, `--no-facegen`,
  `--facegen-size`, …). GUI options: `gui.py` (patch + output_dir fields,
  options row). The preview path (`preview/session.py`) bakes to a temp dir and
  must NOT pack — packing is a Run-only finishing step.

## Design

### 1. `esplib.ba2.Ba2Writer` (the library half)

A new writer in `esplib/src/esplib/ba2.py` (the format already lives there; this
fixes a genuine library gap — keep it in esplib, reusable beyond the furrifier).

```python
class Ba2Writer:
    def __init__(self, archive_type: str, *, compress: bool = True,
                 version: int = 1): ...
    def add_file(self, archive_path: str, data: bytes) -> None: ...   # GNRL
    def add_dds(self, archive_path: str, dds_bytes: bytes) -> None: ...# DX10
    def write(self, out_path) -> None: ...
```

- GNRL: store/zlib each blob, emit records + data + name table. Compute the
  hashes via the verified `ba2_hash` (move it to a shared `_ba2_hash` helper used
  by both writer and any future reader-side validation).
- DX10 (`add_dds`): parse the incoming DDS (magic + 124-byte `DDS_HEADER` +
  optional `DDS_HEADER_DXT10`) to recover width/height/num_mips/dxgi_format and
  the raw mip body (everything after the header). **v1 chunking: a single chunk**
  holding the whole mip pyramid (`start_mip=0`, `end_mip=num_mips-1`) — simplest
  valid layout; small face textures don't need multi-chunk. (Fallback if the
  engine rejects single-chunk: split into per-mip chunks. Validate early — see
  Risks.) DXGI format byte comes straight from the DXT10 header (our diffuse is
  BC7 = 98); read it, don't assume, so non-BC7 inputs still pack.
- Reuse the existing block-format tables (`_BC_BLOCK_BYTES`, `_UNCOMPRESSED_BPP`)
  if any size math is needed.

Place `add_file`/`add_dds` so paths are normalized to backslashes; reject mixed
GNRL/DX10 in one archive.

### 2. furrifier pack step (the application half)

New `furrifier_fo4/pack.py`: `pack_facegen(out_root, patch_name) -> list[Path]`.
- Walk `out_root/meshes/Actors/Character/FaceGenData/FaceGeom/**/*.nif` → a GNRL
  `Ba2Writer`; archive path = the path relative to `out_root` (so
  `Meshes\Actors\Character\FaceGenData\FaceGeom\Fallout4.esm\<fid>.nif`).
- Walk `out_root/textures/Actors/Character/FaceCustomization/**/*.dds` → a DX10
  `Ba2Writer`.
- Write `out_root/<stem> - Main.ba2` and `<stem> - Textures.ba2` where
  `stem = Path(patch_name).stem`.
- **Then remove the loose facegen trees** (loose files override archives in FO4,
  so leaving them would make the BA2 dead weight). Only remove what we packed.
  Skip an archive (and don't delete its tree) if it would be empty.
- Hook in `session.run` right after `build_facegen_for_patch`, gated on a new
  `pack: bool = False` param. Return the archive paths in `stats` for the
  CLI/GUI "Done" summary.

### 3. CLI + GUI surface

- `config.py`: `--pack` flag (`action="store_true"`, default off) → `Config.pack`
  → `session.run(pack=…)`. Default OFF (loose stays the default; packing is the
  ship-it finishing move).
- `gui.py`: a "Pack facegen into BA2" checkbox on the options row, wired through
  `run_furrification`. Disabled/ignored when facegen is off.

## Implementation phases (TDD per `feedback_tdd_workflow`)

1. **`_ba2_hash` + table** in esplib, with a unit test that reproduces the
   verified vanilla hashes (pin a few known `name → hash` pairs from
   `Fallout4 - Interface.ba2`, e.g. `Interface\BarterMenu.swf` →
   name_hash 0xEF32EBB6, dir_hash 0xD2FDF873). Hermetic (hardcode the pairs; no
   game files needed at test time).
2. **GNRL `Ba2Writer`** + **round-trip test**: write a GNRL archive of a few
   blobs, read it back with `Ba2Reader`, assert bytes + paths + `has_file`.
   Compressed and stored variants.
3. **DX10 `Ba2Writer`** + round-trip test: take a known BC7 .dds (a small
   committed fixture), `add_dds`, read back via `Ba2Reader._extract_dx10`, assert
   the reconstructed DDS round-trips (header fields + mip body equal). This
   leans on the reader as the oracle — the two are exact inverses.
4. **`pack.py`** + test over a tiny synthetic output tree (a couple of fake
   nif/dds files): asserts both archives created, archive paths correct, loose
   trees removed, empty-tree skip.
5. **Wire `session.run(pack=)`, `--pack`, GUI checkbox.** Smoke via a `--npcs`
   one-NPC run with `--pack`.
6. **In-game validation (Hugh):** a small `--pack` run, drop the esp + two BA2s
   in Data with NO loose facegen, confirm furrified faces load in-game (Diamond
   City). This is the real test that the hash + DX10 chunking are engine-correct.

## Validation

- **Self round-trip** (phases 2-3): our `Ba2Reader` reads back everything the
  writer emits — strong, cheap, hermetic.
- **Cross-check vs a known tool** (optional): open a furrifier-written BA2 in
  Archive2/BSArch/xEdit; confirm it lists files and extracts identically.
- **In-game** (phase 6): the only true test of hash + single-chunk DX10
  acceptance. Use the decode-and-VIEW technique if a face renders wrong.

## Risks / open questions

- **DX10 single-chunk acceptance.** v1 packs all mips in one chunk. Widely
  reported to work for normal textures, but verify in-game phase 6; fall back to
  per-mip chunking if faces are black/untextured only when archived.
- **Header `version` + `chunk_hdr_size`.** Plan emits version=1, chunk_hdr_size
  =24. Confirm both against a vanilla archive before shipping (the reader
  discards them; add a 2-line spike to print them).
- **Auto-load limits.** FO4 auto-loads `<stem> - Main/Textures.ba2` for an
  enabled plugin. No action needed for one mod's two archives, but note the
  game's total-archive ceiling exists for users with huge load orders.
- **Loose vs archive precedence.** We delete the loose facegen after packing so
  the archive is authoritative. If a user re-runs WITHOUT `--pack` later, fresh
  loose files will (correctly) override the stale archive — document this.
- **Plugin compression flag.** Some setups need the esp's archive-bit / naming
  exact. `<stem> - Main.ba2` is the standard; no esp header flag required for
  auto-load in FO4 (unlike SSE's `.bsa` heuristics). Confirm in phase 6.

## Out of scope

- Packing anything other than the run's facegen (no general asset archiving).
- The preview path (always loose temp).
- Skyrim/BSA writing (the Skyrim furrifier ships loose; a BSA writer is a
  separate, later item if wanted — the `_ba2_hash`/writer split keeps the door
  open).
