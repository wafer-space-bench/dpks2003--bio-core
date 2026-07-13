# chip_top_gl_ext.py — extended gate-level soak suite for chip_top.
#
# Each test states the GL-UNIQUE thing it verifies (netlist/macro/ICG/pad
# interactions that RTL sim cannot exercise). Logic-only corner cases stay
# in the RTL suites.
#
# Run:
#   GL=1 POWER=0 NETLIST=<nl.v> PDK_ROOT=... \
#     COCOTB_TEST_MODULES=chip_top_gl_ext python3 chip_top_tb.py
#   (or add a runner __main__ mirroring chip_top_tb.py)

import cocotb
from cocotb.triggers import Timer
from gl_common import start_up, pad_bit, read_gpio, BidirDriver, GPIO_BASE

QDIV = 100                      # quantum = 10us @10MHz


async def load_and_check(spi, dut, program, core=0):
    await spi.bio_write(0xE0, core)
    await spi.bio_imem_burst_write(0, program)
    for a in (0, len(program) - 1):
        got = await spi.bio_imem_read(a)
        assert got == program[a], f"core{core} load: word {a}=0x{got:08X}"


# ---------------------------------------------------------------------------
@cocotb.test()
async def gl_fifo_host_bitbang(dut):
    """GL-unique: FIFO-blocking stall through the real ICG cells.
    Core blocks on empty FIFO0 (x16 read) -> clock gated by icgtp; host push
    ungates it. Complements test_4 (quanta stall). Verbatim port of
    test_bunnie_fifo_host_bitbang."""
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0x00000000)

    program = [
        0xFFF00293,  # li x5, -1
        0x00028C13,  # mv x24, x5   (all output)
        0x00028D13,  # mv x26, x5   (mask = all)
        # loop:
        0x00080313,  # mv x6, x16   (read FIFO0 -> x6, BLOCKS when empty)
        0x00030A93,  # mv x21, x6   (write GPIO)
        0x00000A13,  # mv x20, x0   (quantum wait)
        0xFF5FF06F,  # j loop
    ]
    await load_and_check(spi, dut, program, core=0)

    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x00, 0x00000005)          # restart0|en0
    await Timer(50_000, unit="ns")

    test_values = [0x0000F1F0, 0x00001234, 0x0000ABCD, 0x00008000]
    for val in test_values:
        await spi.bio_write(0x10, val)             # push FIFO0
        await Timer(QDIV * 3 * 100, unit="ns")     # ~3 quanta
        got = read_gpio(dut) & 0xFFFF
        exp = val & 0xFFFF
        assert got == exp, f"pushed 0x{val:08X}, pads=0x{got:04X} != 0x{exp:04X}"
        dut._log.info(f"  push 0x{val:04X} -> pads 0x{got:04X} OK")

    # blocking check: no push for a while -> GPIO must HOLD last value
    await Timer(QDIV * 6 * 100, unit="ns")
    got = read_gpio(dut) & 0xFFFF
    assert got == test_values[-1] & 0xFFFF, \
        f"core did not block on empty FIFO (pads=0x{got:04X})"
    dut._log.info("core blocked on empty FIFO — stall/ungate via icgtp verified")

    await spi.bio_write(0x00, 0x00000000)


# ---------------------------------------------------------------------------
@cocotb.test()
async def gl_two_cores_concurrent(dut):
    """GL-unique: both ICGs live simultaneously; shared GPIO aggregators
    (with the new en_sync gating) arbitrating two real requesters with gate
    delays. Core0 drives gpio[15:0] pattern A, core1 drives gpio[31:16]
    pattern B, disjoint masks."""
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0x00000000)

    def core_prog(mask_imm_hi):   # set mask+dir to one half, write pattern, loop
        # x26 (mask) and x24 (dir) restricted to the core's half
        return [
            0xFFF00293,                        # li x5, -1
            # build half-mask in x5 via lui-style: use two instrs
            (0x000002B7 | (mask_imm_hi << 12)),# lui x5, imm  (upper half sel)
            0x00028C13,                        # mv x24, x5 (dir = half)
            0x00028D13,                        # mv x26, x5 (mask = half)
            0x00028A93,                        # mv x21, x5 (drive all my pins 1)
            0x00000A13,                        # mv x20, x0 (quantum)
            0xFF9FF06F,                        # j -8 (loop on write+wait)
        ]

    # core0: lower half 0x0000FFFF -> needs li, not lui; simpler: distinct constants
    prog0 = [
        0x000012B7,      # lui x5, 0x1        -> x5 = 0x00001000
        0xFFF28293,      # addi x5, x5, -1    -> x5 = 0x00000FFF
        0x00028C13,      # mv x24, x5  (dir  = 0x0FFF)
        0x00028D13,      # mv x26, x5  (mask = 0x0FFF)
        0x00028A93,      # mv x21, x5  (gpio = 0x0FFF)
        0x00000A13,      # quantum
        0xFF9FF06F,      # j loop
    ]
    prog1 = [
        0xFF0002B7,      # lui x5, 0xFF000    -> x5 = 0xFF000000
        0x00028C13,      # mv x24, x5  (dir  = 0xFF000000)
        0x00028D13,      # mv x26, x5  (mask = 0xFF000000)
        0x00028A93,      # mv x21, x5  (gpio = 0xFF000000)
        0x00000A13,      # quantum
        0xFF9FF06F,      # j loop
    ]

    await load_and_check(spi, dut, prog0, core=0)
    await load_and_check(spi, dut, prog1, core=1)

    await spi.bio_write(0x50, QDIV << 16)          # div core0
    await spi.bio_write(0x54, QDIV << 16)          # div core1
    await spi.bio_write(0x00, 0x0000000F)          # restart both | en both
    await Timer(500_000, unit="ns")

    
    raw = str(dut.bidir_PAD.value)
    dut._log.info(f"raw bidir_PAD = {raw}")
    dut._log.info(f"x_seen after loads = {spi.x_seen}")
    g = read_gpio(dut)
    dut._log.info(f"concurrent gpio = 0x{g:08X}")

    
    assert (g & 0x00000FFF) == 0x00000FFF, f"core0 half wrong: 0x{g:08X}"
    assert (g & 0xFF000000) == 0xFF000000, f"core1 half wrong: 0x{g:08X}"
    assert (g & 0x00FFF000) == 0, f"unclaimed bits driven: 0x{g:08X}"

    # isolation on disable: kill core1, its pins must release; core0 unaffected
    await spi.bio_write(0x00, 0x00000001)          # en0 only
    await Timer(50_000, unit="ns")
    g2 = read_gpio(dut)
    assert (g2 & 0x00000FFF) == 0x00000FFF, "core0 disturbed by core1 disable"
    dut._log.info("two-core concurrent + disable isolation OK")
    await spi.bio_write(0x00, 0x00000000)


# ---------------------------------------------------------------------------
@cocotb.test()
async def gl_event_stall(dut):
    """GL-unique: the third stall flavor (event wait) through the icgtp, and
    the event aggregator with a HOST writer waking a CORE waiter.
    Core: mask event bit0 (x27), wait (x30 read stalls), then drive GPIO."""
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0x00000000)
    await spi.bio_write(0x3C, 0x00FFFFFF)          # clear events

    program = [
        0xFFF00293,      # li x5, -1
        0x00028C13,      # mv x24, x5 (dir all)
        0x00028D13,      # mv x26, x5 (mask all)
        0x00100313,      # li x6, 1
        0x00030D93,      # mv x27, x6  (event_mask = bit0)
        0x000F0393,      # mv x7, x30  (WAIT: stalls until event&mask != 0)
        0x00500A93,      # li x21, 5   -> gpio = 0x5 (marker: we woke up)
        0x0000006F,      # j .  (park)
    ]
    await load_and_check(spi, dut, program, core=0)

    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x00, 0x00000005)
    await Timer(100_000, unit="ns")

    g = read_gpio(dut)
    assert g != 0x5, f"core did not stall on event wait (gpio=0x{g:08X})"
    dut._log.info("core stalled on event — now waking it via host")

    await spi.bio_write(0x38, 0x00000001)          # host sets event bit 0
    await Timer(100_000, unit="ns")
    g = read_gpio(dut)
    assert g == 0x5, f"core did not wake on event (gpio=0x{g:08X})"
    dut._log.info("event stall + host wake through icgtp verified")
    await spi.bio_write(0x00, 0x00000000)
    await spi.bio_write(0x3C, 0x00FFFFFF)


# ---------------------------------------------------------------------------
@cocotb.test()
async def gl_fifo_restart_policy(dut):
    """GL confirmation of the per-core FIFO restart nets (they sit in the ICG
    E-cone in this netlist — verify the policy survived synthesis). Compact
    version of test_fifo_reset."""
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0x00000000)

    for i, addr in enumerate([0x10, 0x14, 0x18, 0x1C]):
        await spi.bio_write(addr, 0xF1F00000 + i)

    v = await spi.bio_read_double(0x0C)
    lv = [(v >> (4 * i)) & 0xF for i in range(4)]
    assert lv == [1, 1, 1, 1], f"fill levels {lv}"

    await spi.bio_write(0x00, 0x00000008)          # restart core1 only
    await Timer(2_000, unit="ns")
    v = await spi.bio_read_double(0x0C)
    lv = [(v >> (4 * i)) & 0xF for i in range(4)]
    assert lv == [1, 0, 1, 1], f"restart core1: levels {lv} (want [1,0,1,1])"
    dut._log.info("per-core FIFO restart policy verified at GL")


# ---------------------------------------------------------------------------

@cocotb.test()
async def gl_core1_solo(dut):
    """Isolation: core 1 alone, its first-ever solo enable."""
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0x00000000)
    prog1 = [0xFF0002B7, 0x00028C13, 0x00028D13, 0x00028A93, 0x00000A13, 0xFF9FF06F]
    await spi.bio_write(0xE0, 1)
    await spi.bio_imem_burst_write(0, prog1)
    await spi.bio_write(0x54, 100 << 16)           # DIV1 — see check 3!
    await spi.bio_write(0x00, 0x0000000A)          # restart1|en1
    await Timer(200_000, unit="ns")
    dut._log.info(f"raw = {str(dut.bidir_PAD.value)}")
    g = read_gpio(dut)
    assert (g & 0xFF000000) == 0xFF000000, f"core1 solo: 0x{g:08X}"


@cocotb.test()
async def gl_gpio_input_path(dut):
    """GL-unique: bidir pad as INPUT — TB drives a pad, signal traverses the
    input buffer -> synchronizers -> core reads x21 -> core echoes to an
    output pin. First test ever of the pad-ring input direction for GPIO."""
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0x00000000)

    # Core: dir = bit1 only (gpio[1] out, gpio[0] stays input);
    # loop: read pins (x21), AND bit0, shift to bit1, write, quantum, loop
    program = [
        0x00200293,      # li x5, 2
        0x00028C13,      # mv x24, x5   (dir = bit1 only)
        0xFFF00313,      # li x6, -1
        0x00030D13,      # mv x26, x6   (mask = all)
        # loop:
        0x000A8393,      # mv x7, x21   (read gpio pins)
        0x0013F393,      # andi x7, x7, 1
        0x00139393,      # slli x7, x7, 1
        0x00038A93,      # mv x21, x7   (gpio[1] <= gpio[0])
        0x00000A13,      # quantum
        0xFEDFF06F,      # j loop
    ]
    await load_and_check(spi, dut, program, core=0)

    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x00, 0x00000005)
    await Timer(50_000, unit="ns")

    drv = BidirDriver(dut.bidir_PAD)
    for din in (1, 0, 1, 1, 0):
        drv.set_bit(GPIO_BASE + 0, din)            # drive gpio[0] pad
        await Timer(QDIV * 4 * 100, unit="ns")     # a few quanta to propagate
        out = pad_bit(dut, GPIO_BASE + 1)
        assert out == din, f"echo failed: drove {din}, gpio[1] pad = {out!r}"
        dut._log.info(f"  gpio[0]={din} -> gpio[1]={out} OK")

    drv.release_all()
    dut._log.info("pad-ring GPIO input path verified at GL")
    await spi.bio_write(0x00, 0x00000000)

@cocotb.test()
async def gl_first_x_hunt(dut):
    """core1_solo replay + first-X monitor. Flat-netlist-safe signal discovery."""
    from cocotb.triggers import RisingEdge

    WANT = ["en_sync", "a_restart", "_000047_", "_000046_",
            "mach[1].core.clk", "mach[0].core.clk", "latched_stalu",
            "fifo_restart", "en_latched"]
    watch = {}
    for handle in dut:                     # iterate top-level children (flat netlist)
        n = handle._name
        if any(w in n for w in WANT):
            try:
                _ = handle.value           # only watch value-readable nets
                watch[n] = handle
            except Exception:
                pass
    dut._log.info(f"watching {len(watch)} signals: {sorted(watch)[:20]} ...")

    first_x = []
    async def monitor():
        clean = set(watch)
        while clean:
            await RisingEdge(dut.clk_PAD)
            t = cocotb.utils.get_sim_time("ns")
            for n in list(clean):
                if "x" in str(watch[n].value).lower():
                    dut._log.warning(f"X-ONSET @{t}ns : {n}")
                    first_x.append((t, n))
                    clean.discard(n)
    cocotb.start_soon(monitor())

    spi = await start_up(dut)
    await spi.bio_write(0x00, 0)
    prog = [0xFF0002B7, 0x00028C13, 0x00028D13, 0x00028A93, 0x00000A13, 0xFF9FF06F]
    await spi.bio_write(0xE0, 1)
    await spi.bio_imem_burst_write(0, prog)
    await spi.bio_write(0x54, 100 << 16)
    dut._log.info(f"=== ENABLE WRITE NOW (~{cocotb.utils.get_sim_time('ns')}ns) ===")
    await spi.bio_write(0x00, 0x0A)
    await Timer(200_000, unit="ns")
    dut._log.info(f"final raw = {str(dut.bidir_PAD.value)}")
    for t, n in sorted(first_x)[:10]:
        dut._log.info(f"  X order: {t}ns  {n}")

@cocotb.test()
async def gl_enable_probe(dut):
    """Cycle-by-cycle table of E / en_sync / restart / a_restart around the
    core-1 enable write. Run STANDALONE (TESTCASE=gl_enable_probe) — shared-sim
    contamination from earlier failing tests would pollute it."""
    from cocotb.triggers import RisingEdge

    # --- signal discovery on the flat netlist ---
    watch = {}
    for handle in dut:
        n = handle._name
        if n == "_000046_":
            watch["E_core1"] = handle
        elif "en_sync" in n:
            watch[f"en_sync:{n[-6:]}"] = handle          # short label
        elif "a_restart_q" in n and "[1]" in n:
            watch[f"aq:{n[-12:]}"] = handle
        elif n.endswith("a_restart[1]"):
            watch["a_restart1"] = handle
        elif "restart" in n and "ctl" in n:              # ctl register restart bits
            watch[f"ctl:{n[-10:]}"] = handle
        elif "fifos[0].fifo_restart" in n:
            watch["restart1(alias)"] = handle            # fifo_restart[1] == restart[1]
    dut._log.info(f"probe signals: {sorted(watch)}")

    printing = False
    count = [0]

    async def probe():
        while True:
            await RisingEdge(dut.clk_PAD)
            if printing and count[0] < 45:
                t = cocotb.utils.get_sim_time("ns")
                vals = "  ".join(f"{k}={str(v.value)}" for k, v in sorted(watch.items()))
                dut._log.info(f"  probe @{t:>10.0f}: {vals}")
                count[0] += 1
    cocotb.start_soon(probe())

    # --- core1_solo body ---
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0)
    prog = [0xFF0002B7, 0x00028C13, 0x00028D13, 0x00028A93, 0x00000A13, 0xFF9FF06F]
    await spi.bio_write(0xE0, 1)
    await spi.bio_imem_burst_write(0, prog)
    await spi.bio_write(0x54, 100 << 16)

    printing = True                       # open the window just before enable
    dut._log.info("=== ENABLE WRITE ===")
    await spi.bio_write(0x00, 0x0A)
    await Timer(10_000, unit="ns")        # ~100 cycles; printing self-limits to 45
    printing = False
    dut._log.info(f"final raw = {str(dut.bidir_PAD.value)}")

@cocotb.test()

async def gl_enable_probe2(dut):
    """Ring-buffer probe: dumps N cycles around E_core1's X transition."""
    from cocotb.triggers import RisingEdge
    from collections import deque

    watch = {}
    for handle in dut:
        n = handle._name
        if n == "_000046_":                      watch["E1"] = handle
        elif n == "_000047_":                    watch["E0"] = handle
        elif "en_sync" in n:                     watch["ensync"] = handle
        elif n.endswith("a_restart[1]"):         watch["arst1"] = handle
        elif "a_restart_q[0][1]" in n:           watch["aq0"] = handle
        elif "a_restart_q[1][1]" in n:           watch["aq1"] = handle
        elif "fifos[1].fifo_restart" in n:       watch["rst1"] = handle
        elif "mach[1].core.latched_stalu" in n:  watch["stalu1"] = handle
    dut._log.info(f"probe2 signals: {sorted(watch)}")

    ring = deque(maxlen=25)
    state = {"armed": False, "fired": False, "post": 0}

    async def probe():
        while True:
            await RisingEdge(dut.clk_PAD)
            t = cocotb.utils.get_sim_time("ns")
            row = f"@{t:>9.0f}: " + "  ".join(
                f"{k}={str(v.value)}" for k, v in sorted(watch.items()))
            ring.append(row)
            e1 = str(watch["E1"].value).lower()
            if state["armed"] and not state["fired"] and "x" in e1:
                state["fired"] = True
                dut._log.warning("=== E1 X TRANSITION — last 25 cycles: ===")
                for r in ring:
                    dut._log.info("  " + r)
            elif state["fired"] and state["post"] < 15:
                dut._log.info("  post " + row)
                state["post"] += 1
    cocotb.start_soon(probe())

    spi = await start_up(dut)
    await spi.bio_write(0x00, 0)
    prog = [0xFF0002B7, 0x00028C13, 0x00028D13, 0x00028A93, 0x00000A13, 0xFF9FF06F]
    await spi.bio_write(0xE0, 1)
    await spi.bio_imem_burst_write(0, prog)
    await spi.bio_write(0x54, 100 << 16)

    state["armed"] = True                 # arm AFTER idle: only the strobe-time X fires
    dut._log.info("=== ARMED, issuing enable write ===")
    await spi.bio_write(0x00, 0x0A)
    await Timer(20_000, unit="ns")
    dut._log.info(f"fired={state['fired']}  final raw = {str(dut.bidir_PAD.value)}")

@cocotb.test()
async def gl_stall_term_hunt(dut):
    """Which stall-cone term goes X first, cycle-aligned with E going X.
    Run STANDALONE. Reproduces gl_fifo_host_bitbang's enable (core 0)."""
    from cocotb.triggers import RisingEdge
    from collections import deque

    PATTERNS = [   # label : substring to find in flat-netlist names (mach 0 focus)
        ("E0",        "_000046_"),
        ("qhalt0",    "mach[0].quanta_halt"),        # name may differ — see note
        ("quantum0",  "quantum[0]"),
        ("rfrd0",     "mach_regfifo_rd[0]"),
        ("rfwr0",     "mach_regfifo_wr[0]"),
        ("rdable",    "regfifo_readable"),
        ("wrable",    "regfifo_writable"),
        ("ev0",       "stalling_for_event[0]"),
        ("ensync",    "en_sync"),
        ("dec_rs1",   "mach[0].core.decoded_rs1"),
        ("dec_rs2",   "mach[0].core.decoded_rs2"),
        ("cpu_st",    "mach[0].core.cpu_state"),
        ("extq0",     "extclk_selected_q[0]"),
        ("stalu0",    "mach[0].core.latched_stalu"),
    ]
    watch = {}
    for handle in dut:
        n = handle._name
        for label, pat in PATTERNS:
            if pat in n:
                watch.setdefault(label, []).append(handle)   # multi-bit: keep all
    dut._log.info(f"found: { {k: len(v) for k, v in watch.items()} }")

    ring = deque(maxlen=20)
    state = {"armed": False, "fired": False, "post": 0}

    def row():
        t = cocotb.utils.get_sim_time("ns")
        parts = []
        for k in sorted(watch):
            vals = "".join(str(h.value) for h in watch[k])
            parts.append(f"{k}={vals}")
        return f"@{t:>9.0f}: " + "  ".join(parts)

    async def mon():
        while True:
            await RisingEdge(dut.clk_PAD)
            r = row()
            ring.append(r)
            e0 = "".join(str(h.value) for h in watch.get("E0", []))
            if state["armed"] and not state["fired"] and "x" in e0.lower():
                state["fired"] = True
                dut._log.warning("=== E0 X — last 20 cycles: ===")
                for rr in ring: dut._log.info("  " + rr)
            elif state["fired"] and state["post"] < 12:
                dut._log.info("  post " + r); state["post"] += 1
    cocotb.start_soon(mon())

    # --- bitbang enable sequence (core 0) ---
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0)
    prog = [0xFFF00293, 0x00028C13, 0x00028D13,
            0x00080313, 0x00030A93, 0x00000A13, 0xFF5FF06F]
    await spi.bio_write(0xE0, 0)
    await spi.bio_imem_burst_write(0, prog)
    await spi.bio_write(0x50, 100 << 16)
    state["armed"] = True
    dut._log.info("=== ARMED, enabling core 0 ===")
    await spi.bio_write(0x00, 0x05)
    await Timer(30_000, unit="ns")
    dut._log.info(f"fired={state['fired']}  raw={str(dut.bidir_PAD.value)}")

@cocotb.test()
async def gl_stall_term_hunt2(dut):
    """Which stall-cone term goes X first, cycle-aligned with the ICG output.
    Run STANDALONE on the CTS netlist. Triggers on mach[0].core.clk going X.
    Reproduces gl_fifo_host_bitbang's enable sequence (core 0).

    NOTE: E-net names (_000048_/_000049_) are synthesis-run-specific. Verify with:
      grep -A3 "icgtp_1 " <nl.v> | grep "\\.E("
    """
    from cocotb.triggers import RisingEdge
    from collections import deque

    EXACT = {
        "clk0": "i_chip_core.u_bio_lite.u_bio.mach[0].core.clk",
        "clk1": "i_chip_core.u_bio_lite.u_bio.mach[1].core.clk",
    }
    SUBSTR = [
        ("ensync",  "en_sync"),
        ("stalu1",  "mach[1].core.latched_stalu"),      # is core 1 X? (expect yes)
        ("gdir",    "u_bio.gpio_dir"),                  # the scc_ff Q — may survive
        ("gout",    "u_bio.gpio_out"),
        ("gdirsnap","gpio_dir_snap"),                   # if you renamed for invert
        ("agg",     "gpdir_set_agg"),                   # aggregator outputs
        ("aggc",    "gpdir_clr_agg"),
    ]

    watch = {}
    for handle in dut:
        n = handle._name
        for label, exact in EXACT.items():
            if n == exact:
                watch.setdefault(label, []).append(handle)
        for label, pat in SUBSTR:
            if pat in n and "clkbuf" not in n and "clknet" not in n:
                watch.setdefault(label, []).append(handle)

    # keep only handles that are readable signals (drop instances etc.)
    for label in list(watch):
        good = []
        for h in watch[label]:
            try:
                _ = h.value
                good.append(h)
            except Exception:
                pass
        if good:
            watch[label] = good
        else:
            del watch[label]

    dut._log.info(f"found: { {k: len(v) for k, v in watch.items()} }")
    wanted = list(EXACT) + [l for l, _ in SUBSTR]
    missing = [l for l in wanted if l not in watch]
    if missing:
        dut._log.warning(f"NOT FOUND (dissolved into _NNN_ nets): {missing}")

    def vals(label):
        return "".join(str(h.value) for h in watch.get(label, []))

    def row():
        t = cocotb.utils.get_sim_time("ns")
        parts = [f"{k}={vals(k)}" for k in sorted(watch)]
        return f"@{t:>9.0f}: " + "  ".join(parts)

    ring = deque(maxlen=20)
    state = {"armed": False, "fired": False, "post": 0}

    TRIG = "clk0" if "clk0" in watch else ("E0" if "E0" in watch else None)
    assert TRIG, "no trigger signal found — check net names"
    dut._log.info(f"trigger signal: {TRIG}")

    async def mon():
        while True:
            await RisingEdge(dut.clk_PAD)
            r = row()
            ring.append(r)
            if state["armed"] and not state["fired"] and "x" in vals(TRIG).lower():
                state["fired"] = True
                dut._log.warning(f"=== {TRIG} X — last 20 cycles: ===")
                for rr in ring:
                    dut._log.info("  " + rr)
            elif state["fired"] and state["post"] < 12:
                dut._log.info("  post " + r)
                state["post"] += 1
    cocotb.start_soon(mon())

    # --- bitbang enable sequence (core 0) ---
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0)
    prog = [0xFFF00293, 0x00028C13, 0x00028D13,
            0x00080313, 0x00030A93, 0x00000A13, 0xFF5FF06F]
    await spi.bio_write(0xE0, 0)
    await spi.bio_imem_burst_write(0, prog)
    await spi.bio_write(0x50, 100 << 16)

    state["armed"] = True
    dut._log.info("=== ARMED, enabling core 0 ===")
    await spi.bio_write(0x00, 0x05)
    await Timer(30_000, unit="ns")
    dut._log.info(f"fired={state['fired']}  raw={str(dut.bidir_PAD.value)}")

@cocotb.test()
async def gl_divider_x_probe(dut):
    """Is pio_divider the X source? Watch penable + divider internals.
    Run STANDALONE on the CTS netlist (window removed, en_sync gating in)."""
    from cocotb.triggers import RisingEdge
    from collections import deque

    SUBSTR = [
        ("penable",  "u_bio.penable"),
        ("pen",      "pio_div"),          # broad: catch divider internals
        ("divcnt",   "div_counter"),
        ("oldpen",   "old_pen"),
        ("divintr",  "div_int_r"),
        ("divfracr", "div_frac_r"),
        ("ensync",   "en_sync"),
        ("clk0",     "mach[0].core.clk"),
    ]
    watch = {}
    for handle in dut:
        n = handle._name
        for label, pat in SUBSTR:
            if pat in n and "clkbuf" not in n and "clknet" not in n:
                watch.setdefault(label, []).append(handle)
    for label in list(watch):
        good = []
        for h in watch[label]:
            try:
                _ = h.value; good.append(h)
            except Exception:
                pass
        if good: watch[label] = good
        else:    del watch[label]

    dut._log.info(f"found: { {k: len(v) for k, v in watch.items()} }")
    missing = [l for l, _ in SUBSTR if l not in watch]
    if missing:
        dut._log.warning(f"NOT FOUND: {missing}")

    def vals(k): return "".join(str(h.value) for h in watch.get(k, []))

    async def mon():
        n = 0
        while n < 40:
            await RisingEdge(dut.clk_PAD)
            t = cocotb.utils.get_sim_time("ns")
            parts = [f"{k}={vals(k)}" for k in sorted(watch)]
            dut._log.info(f"  @{t:>9.0f}: " + "  ".join(parts))
            n += 1
    # sample right after reset release — that's where the X lives
    spi = await start_up(dut)
    cocotb.start_soon(mon())
    await Timer(5_000, unit="ns")
    dut._log.info(f"pads after reset = {str(dut.bidir_PAD.value)}")
@cocotb.test()
async def gl_when_x_appears(dut):
    """Bracket WHICH operation introduces X on the pads."""
    def pads(): return str(dut.bidir_PAD.value)
    spi = await start_up(dut)
    dut._log.info(f"after reset:        {pads()}")
    await spi.bio_write(0x00, 0)
    dut._log.info(f"after ctl=0:        {pads()}")
    prog = [0xFFF00293, 0x00028C13, 0x00028D13,
            0x00080313, 0x00030A93, 0x00000A13, 0xFF5FF06F]
    await spi.bio_write(0xE0, 0)
    dut._log.info(f"after imem sel:     {pads()}")
    await spi.bio_imem_burst_write(0, prog)
    dut._log.info(f"after prog load:    {pads()}")
    await spi.bio_write(0x50, 100 << 16)
    dut._log.info(f"after div write:    {pads()}")
    await spi.bio_write(0x00, 0x05)
    dut._log.info(f"immediately after enable: {pads()}")
    for i in range(6):
        await Timer(5_000, unit="ns")
        dut._log.info(f"  +{(i+1)*5}us: {pads()}")
@cocotb.test()
async def gl_valid_x_probe(dut):
    """Watch the aggregator strobes at the enable moment."""
    from cocotb.triggers import RisingEdge
    from collections import deque

    SUBSTR = [
        ("gset_v",   "gpio_set_valid"),
        ("gclr_v",   "gpio_clr_valid"),
        ("gdset_v",  "gpdir_set_valid"),
        ("gdclr_v",  "gpdir_clr_valid"),
        ("wen",      "cpuregs.wen"),          # try; may be _NNN_
        ("waddr",    "cpuregs.waddr"),
        ("latst",    "latched_store"),
        ("latrd",    "latched_rd"),
        ("ensync",   "en_sync"),
        ("clk0",     "mach[0].core.clk"),
    ]
    watch = {}
    for handle in dut:
        n = handle._name
        for label, pat in SUBSTR:
            if pat in n and "clkbuf" not in n and "clknet" not in n:
                watch.setdefault(label, []).append(handle)
    for label in list(watch):
        good = []
        for h in watch[label]:
            try: _ = h.value; good.append(h)
            except Exception: pass
        if good: watch[label] = good
        else: del watch[label]
    dut._log.info(f"found: { {k: len(v) for k, v in watch.items()} }")
    dut._log.warning(f"NOT FOUND: {[l for l,_ in SUBSTR if l not in watch]}")

    def vals(k): return "".join(str(h.value) for h in watch.get(k, []))

    printing = [False]
    async def mon():
        n = 0
        while True:
            await RisingEdge(dut.clk_PAD)
            if printing[0] and n < 30:
                t = cocotb.utils.get_sim_time("ns")
                dut._log.info(f"  @{t:>9.0f}: " +
                    "  ".join(f"{k}={vals(k)}" for k in sorted(watch)) +
                    f"  pads={str(dut.bidir_PAD.value)[9:20]}")
                n += 1
    cocotb.start_soon(mon())

    spi = await start_up(dut)
    await spi.bio_write(0x00, 0)
    prog = [0xFFF00293, 0x00028C13, 0x00028D13,
            0x00080313, 0x00030A93, 0x00000A13, 0xFF5FF06F]
    await spi.bio_write(0xE0, 0)
    await spi.bio_imem_burst_write(0, prog)
    await spi.bio_write(0x50, 100 << 16)
    printing[0] = True
    dut._log.info("=== ENABLE ===")
    await spi.bio_write(0x00, 0x05)
    await Timer(5_000, unit="ns")

@cocotb.test()
async def gl_twocore_stage_probe(dut):
    def pads(): return str(dut.bidir_PAD.value)
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0)
    prog0 = [0x000012B7, 0xFFF28293, 0x00028C13, 0x00028D13, 0x00028A93, 0x00000A13, 0xFF9FF06F]
    prog1 = [0xFF0002B7, 0x00028C13, 0x00028D13, 0x00028A93, 0x00000A13, 0xFF9FF06F]
    await spi.bio_write(0xE0, 0); await spi.bio_imem_burst_write(0, prog0)
    await spi.bio_write(0xE0, 1); await spi.bio_imem_burst_write(0, prog1)
    await spi.bio_write(0x50, 100 << 16)
    await spi.bio_write(0x54, 100 << 16)

    await spi.bio_write(0x00, 0x05)              # core 0 first
    await Timer(30_000, unit="ns")
    dut._log.info(f"core0 only:   {pads()}")

    await spi.bio_write(0x00, 0x0F)              # then add core 1
    await Timer(30_000, unit="ns")
    dut._log.info(f"staged both:  {pads()}")