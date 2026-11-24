Using the vendor fitter
#######################

Until an open-source place & route tool exists, fuse maps are produced by the vendor fitters ``fit1502.exe``, ``fit1504.exe`` and ``fit1508.exe`` (run under Wine on non-Windows hosts). ``v2svf.sh`` and ``util.toolchain`` use them with EDIF input. This page collects practical notes on running them.

Fitter versions
===============

The fitters are distributed with two vendor packages, and the two builds are not interchangeable:

* **ProChip Designer** includes the builds listed in ``vendor/README.txt`` together with ``aprim.lib``, the primitive library needed to read EDIF.
* **WinCUPL** includes builds (e.g. version 1.8.7.8 from 2003) that accept ``-i design.tt2`` (the PLA files written by CUPL), and also ``-ifmt EQN``/``TT``/``EDIF``, but ship *without* ``aprim.lib``. With these, EDIF input fails with ``Error - File not found : aprim.lib``.

Running the fitter
==================

* The fitter locates ``atmel.std`` (and ``aprim.lib``) and writes its temporary files (``*.tmp``, ``*.ren``) in the directory named by the ``FITTERDIR`` environment variable, which must therefore be writable. (The WinCUPL installer sets ``FITTERDIR`` in the registry of its own Wine prefix; ``v2svf.sh`` sets it explicitly.) If ``FITTERDIR`` is unset, the root of the current drive is used.
* The fitter runs without a display; only ``wine`` (32-bit) is needed, so it works in a container.

Input formats
=============

TT2 (Berkeley PLA with CUPL extensions) is the most capable input format. As noted in the fuzzers, some features cannot be obtained from EDIF at all: foldback nodes (``fuzzers/024-foldback``) and buried nodes used as global output enables (``fuzzers/021-goe_mux``). A TT2 file lists the pins and buried nodes (``#$ PINS``, ``#$ NODES``, each optionally with a pin or node number), the PLA inputs (``.ilb``: pins, ``NAME.Q`` register feedback, ``NAME.PIN`` pin feedback of bidirectional pins, buried combinational nodes) and the PLA outputs (``.ob``: combinational outputs and nodes, ``NAME.REG``/``NAME.T``/``NAME.L`` storage inputs, ``NAME.C``, ``NAME.CE``, ``NAME.AR``, ``NAME.AP``, ``NAME.LE``, ``NAME.OE``), followed by one product term per line.

A sum-of-products netlist produced by Yosys (``synth``; ``dfflegalize`` to D flip-flops with optional clock enable and asynchronous reset or set, and to latches; ``abc -sop``) maps directly onto this format, which provides a synthesis flow from Verilog that works with both fitter builds.
