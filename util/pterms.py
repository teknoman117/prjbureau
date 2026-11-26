from bitarray import bitarray

from . import toolchain


def buried_range(device_name, device, package, pinout, macrocell_name, code, *, other=None):
    """Find a product term of a macrocell that is not bonded out, using a flip-flop node `dff`
    placed in that macrocell instead of its pin. `code` is Verilog in which `{x}` is replaced by
    CLK1 and then CLK2, so that the product term under test reads a different input in each run;
    `Q` and `D` are declared, and `A`, `B` are spare inputs.

    Inactive bits of an active PT are 1, so ORing the two bitstreams gives an all-ones pattern in
    that PT. If the design also uses another product term (`other`, its known range), that one
    is all-ones except for its own inputs, and is removed from the result."""
    macrocell_idx = list(device['macrocells']).index(macrocell_name)

    def run(x):
        return toolchain.run(
            f"module top(input CLK1, CLK2, A, B); "
            f"wire Q, D; "
            f"{code.format(x=x)} "
            f"endmodule",
            {
                "CLK1": pinout['C1'],
                "CLK2": pinout['C2'],
                "dff": str(601 + macrocell_idx),
            },
            f"{device_name}-{package}")

    f_pt = run("CLK1") | run("CLK2")
    pt_range = range(*device['ranges']['pterms'])
    pt_fuses = f_pt[pt_range.start:pt_range.stop].search(bitarray([1]))
    start, stop = min(pt_fuses), max(pt_fuses) + 1
    if other is not None:
        assert stop - start == 192, f"found {pt_fuses} PT fuses, expected two PTs"
        assert [start, start + 96] == other or [start + 96, stop] == other, \
               f"found PTs {start}..{stop}, expected one of them to be {other}"
        start, stop = (start + 96, stop) if [start, start + 96] == other else (start, start + 96)
    else:
        assert len(pt_fuses) == 96, f"found {pt_fuses} PT fuses, expected 96"
        assert pt_fuses == list(range(start, stop)), "PT fuses not contiguous"
    return [start, stop]
