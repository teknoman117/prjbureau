BE-series programming
#####################

ATF15xxBE devices store their configuration in integrated Flash memory organized as 8-bit words, unlike the wide words of the :doc:`AS series <as>`. They are programmed via JTAG through a 32-word page buffer.

.. caution::

   The description below was obtained from the vendor programming software (Atmel ATMISP 7.3): its programming sequences were reconstructed and the SVF files produced by :doc:`fuseconv <../tools/fuseconv>` program exactly the same words as the SVF files ATMISP writes for the same JED files. It has **not** been tested on BE silicon.

JTAG instructions
=================

BE-series CPLDs have a 10 bit long instruction register. The programming-related instructions are:

.. table::
   :widths: auto

   ===================== ============ ========= ================================================
   Instruction           IR value     DR length Function
   ===================== ============ ========= ================================================
   ``IDCODE``            ``059``      32
   ``ISC_READ_UES``      ``270``      16
   ``ISC_ENABLE``        ``2a4``      10        enter programming mode (DR ``1b9``)
   ``ISC_DISABLE``       ``2a5``      10        leave programming mode (DR ``000``)
   ``ISC_SELECT_BANK1``  ``290``      —         select words 0..4811
   ``ISC_HANDSHAKE``     ``291``      —
   ``ISC_CONTROL``       ``292``      8         ``06`` / ``02`` before a write, ``03`` before a read
   ``ISC_BULK_ERASE``    ``293``      —         wait 1.8 s
   ``ISC_SELECT_BANK2``  ``294``      —         select words 4812.. (ATF1508BE/RE only)
   ``ISC_ADDRESS``       ``2a1``      16        word address, shifted MSB first
   ``ISC_PAGE_DATA``     ``2a2``      256       32 consecutive words
   ``ISC_WORD_DATA``     ``2a3``      8         one word
   ``ISC_PROGRAM``       ``2a6``      —         wait 10 ms
   ===================== ============ ========= ================================================

The names are descriptive; the vendor names are unknown.

Memory layout
=============

.. table::
   :widths: auto

   =========== ===== ================== ============================= ================
   Device      Words Bank 1 pages       Bank 1 single words           Bank 2
   =========== ===== ================== ============================= ================
   ATF1502BE   2090  ``00..40``         ``81e..829``                  —
   ATF1504BE   4252  ``00..83``         ``1080..109b``                —
   ATF1508BE   9226  ``00..95``         ``12c0..12cb``                pages ``00..88``, words ``1120..113d`` (relative to word 4812)
   =========== ===== ================== ============================= ================

Pages and single words overlap (e.g. on ATF1502BE, page ``40`` covers words 2048..2079 and words 2078..2079 are written again individually); this is what the vendor software does. The last words of each device hold the configuration, special-purpose and user signature fuses; there are no separately addressed configuration registers as on the AS series.

Programming flow
================

In all flows, after shifting into IR or DR, enter Run-Test/Idle. *Handshake* means: shift the given select instruction, wait 1 µs.

To program a page of 32 words ``data`` at word address ``32 × p`` (or a single word at address ``a``):

  1. Handshake with the bank select instruction.
  2. Shift ``ISC_CONTROL``, shift ``06``, run 8 and then 10 TCK cycles.
  3. Handshake with ``ISC_HANDSHAKE``, then with the bank select instruction.
  4. Shift ``ISC_CONTROL``, shift ``02``, run 8 and then 5 TCK cycles.
  5. Shift ``ISC_ADDRESS``, shift the address (MSB first), run 16 TCK cycles.
  6. Shift ``ISC_PAGE_DATA`` (``ISC_WORD_DATA``), shift the data, run 256 (8) TCK cycles.
  7. Shift ``ISC_PROGRAM``, wait 10 ms; handshake with ``ISC_HANDSHAKE``.

To read back, use ``ISC_CONTROL`` value ``03`` (once, with 8 TCK cycles), the address as above, and shift the page or word out of ``ISC_PAGE_DATA``/``ISC_WORD_DATA``.

The complete sequence used by ``fuseconv`` is: check IDCODE, enable, bulk erase, disable, enable, program every page and single word of every bank, verify them, disable.

Fuse packing
============

The word containing each fuse is given by segment tables in ``util/device_be.py``; within a word, the bit shifted first is column 7 of the table. A few fuses are stored in two cells and a few (outside the logic) in none.

.. literalinclude:: ../../util/device_be.py
   :linenos:
   :pyobject: ATF1502BEDevice
