"""Translate a netlist of `cells.v` primitives into the TT2 format.

The fitter builds distributed with WinCUPL ship without `aprim.lib`, so they cannot read EDIF.
They do read TT2 (the Berkeley PLA files written by CUPL), so the fuzzers can be run with these
builds by translating the Yosys netlist of the fuzzer's design into TT2 instead.

The translation mirrors what the fitter does with EDIF: combinational cells are collapsed into
a sum of products for each pin and each flip-flop or latch input. A flip-flop, latch or tristate
buffer that drives a top-level output (directly or through buffers) is placed in that pin's
macrocell; any other flip-flop or latch becomes a buried node named after its instance. Any net
or instance named in `pins` keeps its own pin or node.
"""

import json
import os
import re

from . import root_dir


# Combinational cells, read from `cells.v`: every cell whose body is a single `assign` is
# translated by evaluating its expression on sums of products (Verilog's `~`, `&`, `^`, `|` have
# the same precedence as Python's; the `?:` of MUX2 is rewritten first).
def _gates():
    with open(os.path.join(root_dir, "cells.v")) as f:
        source = f.read()
    gates = {}
    for name, ports, body in re.findall(r"^module (\w+)\(([^)]*)\);\n(.*?)^endmodule",
                                        source, re.M | re.S):
        match = re.fullmatch(r"\s*assign (\w+) = ([^;]*);\s*", body)
        if not match or "'b" in match[2]:
            continue
        inputs, outputs, direction = [], [], None
        for token in ports.replace(",", " ").split():
            if token in ("input", "output"):
                direction = token
            else:
                (inputs if direction == "input" else outputs).append(token)
        # the function of the cell's single output port (the port is what the netlist connects,
        # whatever net name the `assign` uses)
        (output,), expr = outputs, match[2]
        expr = re.sub(r"^(\w+) \? (\w+) : (\w+)$", r"(\1 & \2) | (~\1 & \3)", expr)
        code = compile(expr, f"cells.v:{name}", "eval")
        gates[name] = (output, inputs,
                       lambda *args, code=code, inputs=inputs:
                           eval(code, {"__builtins__": {}}, dict(zip(inputs, args))))
    return gates


# Storage cells: input ports, and the TT2 suffix for each of them.
_STORAGE = {
    "DFF":     {"CLK": "C", "D": "REG"},
    "DFFE":    {"CLK": "C", "CE": "CE", "D": "REG"},
    "DFFAR":   {"CLK": "C", "AR": "AR", "D": "REG"},
    "DFFAS":   {"CLK": "C", "AS": "AP", "D": "REG"},
    "DFFARS":  {"CLK": "C", "AR": "AR", "AS": "AP", "D": "REG"},
    "DFFEARS": {"CLK": "C", "AR": "AR", "AS": "AP", "CE": "CE", "D": "REG"},
    "TFF":     {"CLK": "C", "T": "T"},
    "TFFE":    {"CLK": "C", "CE": "CE", "T": "T"},
    "TFFAR":   {"CLK": "C", "AR": "AR", "T": "T"},
    "TFFAS":   {"CLK": "C", "AS": "AP", "T": "T"},
    "TFFARS":  {"CLK": "C", "AR": "AR", "AS": "AP", "T": "T"},
    "TFFEARS": {"CLK": "C", "AR": "AR", "AS": "AP", "CE": "CE", "T": "T"},
    "LATCH":   {"EN": "LE", "AR": "AR", "AS": "AP", "CE": "CE", "D": "L"},
}

# Tristate buffers: data input, enable input, output.
_TRISTATE = {
    "TRI":   ("A", "ENA", "Q"),
    "BUFTH": ("A", "ENA", "Q"),
    "BIBUF": ("A", "EN",  "PAD"),
}


class Sop:
    """A sum of products over named literals; a product is a frozenset of (name, polarity)."""

    def __init__(self, products):
        products = {p for p in products if not any((n, not v) in p for n, v in p)}
        self.products = frozenset(p for p in products if not any(q < p for q in products))

    @classmethod
    def zero(cls):
        return cls([])

    @classmethod
    def one(cls):
        return cls([frozenset()])

    @classmethod
    def literal(cls, name):
        return cls([frozenset([(name, True)])])

    def is_zero(self):
        return not self.products

    def is_one(self):
        return frozenset() in self.products

    def __or__(self, other):
        return Sop(self.products | other.products)

    def __and__(self, other):
        return Sop(p | q for p in self.products for q in other.products)

    def __invert__(self):
        result = Sop.one()
        for p in self.products:
            result &= Sop([frozenset([(n, not v)]) for n, v in p])
        return result

    def __xor__(self, other):
        return (self & ~other) | (~self & other)


class _Netlist:
    def __init__(self, module, pins):
        self.module = module
        self.pins = pins
        self.gates = _gates()
        self.driver = {}                    # bit -> (cell name, port) or ("$port", port name)
        self.cells = module["cells"]
        for name, port in module["ports"].items():
            if port["direction"] in ("input", "inout"):
                for bit in port["bits"]:
                    self.driver.setdefault(bit, ("$port", name))
        for name, cell in self.cells.items():
            for port, bits in cell["connections"].items():
                if cell["port_directions"].get(port) == "output":
                    for bit in bits:
                        self.driver[bit] = (name, port)
        self.readers = {}
        for name, cell in self.cells.items():
            for port, bits in cell["connections"].items():
                if cell["port_directions"].get(port) == "input":
                    for bit in bits:
                        self.readers.setdefault(bit, []).append((name, port))

    def bit(self, cell, port):
        bits = self.cells[cell]["connections"].get(port)
        if not bits:
            return None
        assert len(bits) == 1, f"{cell}.{port} is not a single bit"
        return bits[0]


def translate(netlist, pins):
    """Translate a Yosys JSON netlist (a dict, with `cells.v` cells as black boxes) of a single
    top module into TT2 text. `pins` maps port and instance names to pin or node numbers."""
    (module,) = [m for m in netlist["modules"].values()
                 if not m.get("attributes", {}).get("blackbox")]
    n = _Netlist(module, pins)

    outputs = [name for name, port in module["ports"].items()
               if port["direction"] in ("output", "inout")]
    port_of_bit = {}
    for name in outputs:
        (bit,) = module["ports"][name]["bits"]
        port_of_bit[bit] = name

    def through_buffers(bit):
        # Follow `bit` forward through single-reader buffers to a top-level output.
        seen = set()
        while bit not in port_of_bit and bit not in seen:
            seen.add(bit)
            readers = n.readers.get(bit, [])
            if len(readers) != 1:
                return None
            cell, port = readers[0]
            kind = n.cells[cell]["type"]
            if kind in ("BUF", "OUTBUF") and cell not in pins:
                bit = n.bit(cell, "Q")
            elif kind in _TRISTATE and port == _TRISTATE[kind][0]:
                bit = n.bit(cell, _TRISTATE[kind][2])
            else:
                return None
        return port_of_bit.get(bit)

    # Name every signal that becomes a pin or a node.
    signal_of = {}                          # cell name -> TT2 signal name
    nodes = []
    for name, cell in n.cells.items():
        kind = cell["type"]
        if kind in _STORAGE:
            port = through_buffers(n.bit(name, "Q"))
            if port is not None and name not in pins:
                signal_of[name] = port
            else:
                signal_of[name] = name
                nodes.append(name)
        elif kind in _TRISTATE:
            port = through_buffers(n.bit(name, _TRISTATE[kind][2]))
            assert port is not None, f"tristate buffer {name} does not drive a pin"
            signal_of[name] = port
        elif kind in n.gates:
            if name in pins:
                signal_of[name] = name
                nodes.append(name)
        else:
            raise ValueError(f"cell {name} has unsupported type {kind}")

    pin_of_signal = {**{name: name for name in outputs}}
    storage_pins = {signal_of[name] for name, cell in n.cells.items()
                    if cell["type"] in _STORAGE and signal_of[name] in pin_of_signal}

    # Sums of products for nets, over TT2 input labels.
    memo = {}

    def net(bit):
        if bit in ("0", "x", "z"):
            return Sop.zero()
        if bit == "1":
            return Sop.one()
        if bit in memo:
            return memo[bit]
        if bit not in n.driver:
            raise ValueError(f"net {bit} has no driver")
        cell, port = n.driver[bit]
        if cell == "$port":
            label = f"{port}.PIN" if port in outputs and \
                module["ports"][port]["direction"] == "inout" else port
            result = Sop.literal(label)
        else:
            kind = n.cells[cell]["type"]
            if kind in _STORAGE:
                result = Sop.literal(f"{signal_of[cell]}.Q")
            elif kind in _TRISTATE:
                # the net driven by a tristate buffer (or the output of BIBUF) is the pad
                result = Sop.literal(f"{signal_of[cell]}.PIN")
            elif cell in signal_of:
                result = Sop.literal(signal_of[cell])
            else:
                out, ins, fn = n.gates[kind]
                result = fn(*[net(n.bit(cell, p)) for p in ins])
        memo[bit] = result
        return result

    def gate_function(cell):
        out, ins, fn = n.gates[n.cells[cell]["type"]]
        return fn(*[net(n.bit(cell, p)) for p in ins])

    functions = []                          # (output label, Sop)
    for name in outputs:
        (bit,) = module["ports"][name]["bits"]
        if bit in ("0", "1", "x", "z"):
            functions.append((name, net(bit)))
            continue
        driver = n.driver.get(bit)
        if driver is None or driver == ("$port", name):
            continue
        if any(signal_of.get(c) == name for c, cell in n.cells.items()
               if cell["type"] in _STORAGE or cell["type"] in _TRISTATE):
            continue
        functions.append((name, net(bit)))
    for name, cell in n.cells.items():
        kind = cell["type"]
        if kind in _STORAGE:
            signal = signal_of[name]
            for port, suffix in _STORAGE[kind].items():
                bit = n.bit(name, port)
                if bit is None:
                    continue
                f = net(bit)
                if suffix == "LE":
                    # The enable of the EDIF `LATCH` primitive has the opposite polarity to the
                    # TT2 `.LE` function: with `.LE = EN` the fitter inverts the global clock
                    # that it does not invert for `LATCH` in EDIF (see `003-ff_type`).
                    f = ~f
                if suffix == "CE" and f.is_one():
                    continue
                if suffix in ("AR", "AP") and f.is_zero():
                    continue
                functions.append((f"{signal}.{suffix}", f))
        elif kind in _TRISTATE:
            data, enable, _ = _TRISTATE[kind]
            signal = signal_of[name]
            if signal not in storage_pins:
                functions.append((signal, net(n.bit(name, data))))
            functions.append((f"{signal}.OE", net(n.bit(name, enable))))
        elif name in signal_of:
            functions.append((signal_of[name], gate_function(name)))

    inputs = sorted({lit for _, f in functions for p in f.products for lit, _ in p})
    rows = []
    for index, (label, f) in enumerate(functions):
        # Note that the fitter does not allocate the cubes of a sum to product terms in this order;
        # fuzzers that compare designs with different sums limit the comparison to the
        # macrocell configuration fuses.
        for p in sorted(f.products, key=lambda p: sorted(p)):
            literals = dict(p)
            row = "".join("-" if lit not in literals else "1" if literals[lit] else "0"
                          for lit in inputs)
            rows.append((row, index))

    def decl(name):
        return f"{name}+:{pins[name]}" if name in pins else f"{name}+"

    pin_names = [name for name, port in module["ports"].items()]
    lines = [f"#$ PINS {len(pin_names)} {' '.join(decl(name) for name in pin_names)}"]
    if nodes:
        lines.append(f"#$ NODES {len(nodes)} {' '.join(decl(name) for name in nodes)}")
    lines += [
        f".i {len(inputs)}",
        f".o {len(functions)}",
        ".type f",
        *([f".ilb {' '.join(inputs)}"] if inputs else []),    # CUPL omits it without inputs
        f".ob {' '.join(label for label, _ in functions)}",
        f".phase {'1' * len(functions)}",
        f".p {len(rows)}",
    ]
    for row, index in rows:
        outs = "".join("1" if i == index else "0" for i in range(len(functions)))
        lines.append(f"{row} {outs}")
    lines.append(".e")
    return "\n".join(lines) + "\n"


def translate_file(json_path, pins):
    with open(json_path) as f:
        return translate(json.load(f), pins)
