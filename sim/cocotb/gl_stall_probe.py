# gl_stall_probe.py — stall-cone X-origin hunt for the BIO-lite GL netlist.
#
# Watches EVERY input of the stall equation (quanta_halt, quantum,
# mach_regfifo_rd/wr, regfifo_readable/writable, stalling_for_event,
# en_sync, extclk path) plus the reset-window signals (core_in_reset,
# rst_cnt, core_live) plus the icgtp E/Q/CLK pins (auto-extracted from
# the netlist, so it survives re-synthesis).
#
# Two tests:
#   gl_stall_cone_probe    — replays two_cores_concurrent (the X-flood case),
#                            reports first-X onset ORDER + ring dump around it
#   gl_stall_cone_bitbang  — replays the fifo bitbang FIRST PUSH (the clean-0
#                            functional case), prints a cycle table: does E0
#                            ever reopen after the push?
#
# Run standalone, one at a time:
#   GL=1 POWER=0 PDK_ROOT=... NETLIST=$NL TEST_MODULE=gl_stall_probe \
#     TESTCASE=gl_stall_cone_probe python3 chip_top_tb.py
#
# Send back: the "watching" discovery list, the X-onset order, the ring dump.

import os
import re
from collections import deque

import cocotb
from cocotb.triggers import RisingEdge, ReadOnly, Timer
from gl_common import start_up, read_gpio

QDIV = 100

STALL_CONE_PATTERNS = [
    "quanta_halt", "quantum", "mach_regfifo_rd", "mach_regfifo_wr",
    "regfifo_readable", "regfifo_writable", "stalling_for_event",
    "en_sync", "stall", "extclk_selected", "use_extclk", "penable",
    "core_in_reset", "core_live", "rst_cnt", "fifo_restart",
]


# ------------------------------------------------------------------ helpers

def find_icg_nets(netlist_path):
    """{instance: {pin: net}} for every icgtp cell in the netlist."""
    txt = open(netlist_path).read()
    icgs = {}
    for m in re.finditer(r'icgtp_\d+\s+(\S+)\s*\((.*?)\);', txt, re.S):
        inst, body = m.group(1), m.group(2)
        pins = dict(re.findall(r'\.(\w+)\s*\(\s*([^)\s,]+)\s*\)', body))
        icgs[inst.strip("\\")] = pins
    return icgs


def discover(dut, log):
    """Find handles for the stall cone + ICG pins. Tolerates flat or
    hierarchical netlists: walks down known scopes if they exist."""
    scopes = [dut]
    node = dut
    for name in ("i_chip_core", "u_bio_lite", "u_bio"):
        node = getattr(node, name, None)
        if node is None:
            break
        scopes.append(node)

    icg_nets = {}
    nl = os.environ.get("NETLIST")
    if nl and os.path.exists(nl):
        for inst, pins in find_icg_nets(nl).items():
            short = inst.split(".")[-1]          # e.g. mach[0].icg -> icg
            tag = "icg0" if "[0]" in inst else ("icg1" if "[1]" in inst else short)
            for p in ("E", "Q", "CLK"):
                if p in pins:
                    icg_nets[f"{tag}.{p}"] = pins[p].strip("\\")
        log.info(f"netlist ICGs: {icg_nets}")

    watch = {}
    for scope in scopes:
        for h in scope:
            n = h._name
            hit = None
            if any(p in n for p in STALL_CONE_PATTERNS):
                hit = n
            else:
                for label, net in icg_nets.items():
                    if n == net or n.strip("\\") == net:
                        hit = label
                        break
            if hit is not None:
                try:
                    _ = h.value
                except Exception:
                    continue
                watch.setdefault(hit, h)

    log.info(f"stall-cone probe: watching {len(watch)} signals:")
    for n in sorted(watch):
        log.info(f"    {n}")
    if not watch:
        log.error("NOTHING FOUND — netlist is likely hierarchical elsewhere "
                  "or names were optimized away. Run:  grep -o "
                  "'quanta_halt\\S*\\|en_sync\\S*\\|core_in_reset\\S*' $NETLIST | sort -u")
    return watch


def fmt_row(t, watch):
    cells = "  ".join(f"{k}={str(v.value)}" for k, v in sorted(watch.items()))
    return f"@{t:>10.0f}ns  {cells}"


# ------------------------------------------------------------------ test 1

@cocotb.test()
async def gl_stall_cone_probe(dut):
    """two_cores_concurrent replay + full stall-cone first-X monitor."""
    watch = discover(dut, dut._log)
    assert watch, "no probe signals found — see log for grep hint"

    ring = deque(maxlen=25)
    onsets = []                       # (time, name) in order of X appearance
    state = {"armed": False, "dumped": False, "post": 0}
    clean = set(watch)

    async def monitor():
        while True:
            await RisingEdge(dut.clk_PAD)
            await ReadOnly()                       # settled values only
            t = cocotb.utils.get_sim_time("ns")
            row = fmt_row(t, watch)
            ring.append(row)
            if state["armed"]:
                newly = [n for n in list(clean)
                         if "x" in str(watch[n].value).lower()]
                for n in newly:
                    onsets.append((t, n))
                    clean.discard(n)
                if newly and not state["dumped"]:
                    state["dumped"] = True
                    dut._log.warning(f"=== FIRST X ONSET @{t}ns on: {newly} ===")
                    dut._log.warning("=== last 25 cycles: ===")
                    for r in ring:
                        dut._log.info("  " + r)
                elif state["dumped"] and state["post"] < 15:
                    dut._log.info("  post " + row)
                    state["post"] += 1

    cocotb.start_soon(monitor())

    # ---------------- two_cores_concurrent body ----------------
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0x00000000)

    prog0 = [0x000012B7, 0xFFF28293, 0x00028C13, 0x00028D13,
             0x00028A93, 0x00000A13, 0xFF9FF06F]
    prog1 = [0xFF0002B7, 0x00028C13, 0x00028D13,
             0x00028A93, 0x00000A13, 0xFF9FF06F]

    await spi.bio_write(0xE0, 0)
    await spi.bio_imem_burst_write(0, prog0)
    await spi.bio_write(0xE0, 1)
    await spi.bio_imem_burst_write(0, prog1)

    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x54, QDIV << 16)

    state["armed"] = True
    dut._log.info("=== ARMED — issuing concurrent enable (0x0F) ===")
    await spi.bio_write(0x00, 0x0000000F)
    await Timer(500_000, unit="ns")

    dut._log.info(f"final raw = {str(dut.bidir_PAD.value)}")
    dut._log.info(f"final gpio = 0x{read_gpio(dut):08X}")
    dut._log.warning("=== X onset order (cause is at the top): ===")
    for t, n in onsets[:20]:
        dut._log.warning(f"    {t:>10.0f}ns  {n}")
    if not onsets:
        dut._log.warning("    (no X seen on any watched signal — X is born "
                         "OUTSIDE the stall cone; fall back to VCD)")


# ------------------------------------------------------------------ test 2

@cocotb.test()
async def gl_stall_cone_bitbang(dut):
    """fifo_host_bitbang replay: cycle table around the FIRST FIFO push.
    Question: does E0/Q0 reopen after the push (ungate), and which stall
    term stays stuck if not."""
    watch = discover(dut, dut._log)
    assert watch, "no probe signals found"

    printing = {"on": False, "count": 0}

    async def probe():
        while True:
            await RisingEdge(dut.clk_PAD)
            await ReadOnly()
            if printing["on"] and printing["count"] < 60:
                t = cocotb.utils.get_sim_time("ns")
                dut._log.info("  " + fmt_row(t, watch))
                printing["count"] += 1

    cocotb.start_soon(probe())

    spi = await start_up(dut)
    await spi.bio_write(0x00, 0x00000000)

    program = [0xFFF00293, 0x00028C13, 0x00028D13,
               0x00080313, 0x00030A93, 0x00000A13, 0xFF5FF06F]
    await spi.bio_write(0xE0, 0)
    await spi.bio_imem_burst_write(0, program)

    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x00, 0x00000005)          # restart0|en0
    await Timer(50_000, unit="ns")                 # core reaches FIFO block

    dut._log.info("=== core should now be blocked on empty FIFO0 — "
                  "opening probe window and pushing ===")
    printing["on"] = True
    await spi.bio_write(0x10, 0x0000F1F0)          # the push that should ungate
    await Timer(QDIV * 3 * 100, unit="ns")
    printing["on"] = False

    got = read_gpio(dut) & 0xFFFF
    dut._log.info(f"pads after push = 0x{got:04X} (expect 0xF1F0)")
