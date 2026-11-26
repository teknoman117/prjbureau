# JED <-> SVF fuse packing for the BE-series devices (ATF1502BE, ATF1504BE, ATF1508BE/RE).
#
# Unlike the AS series (util/device.py), BE devices store the configuration in 8-bit words
# ("rows") that are programmed through a page buffer (32 words at a time) or one word at a time,
# with a 16-bit word address. The tables below give, for every word, the JED fuse feeding each of
# its eight cells. They were determined from the SVF files that the Atmel ATMISP 7.3 programming
# software writes: given JED files in which every fuse spells out its own index in binary across
# the set (all 0, all 1, and fuse i = bit k of i), the fuse behind every bit of every scan can be
# read back. They reproduce ATMISP's SVF bit-for-bit for those files and for random fuse maps of
# every BE device. Source: the open-atf150x specification, §5.3 "BE-type families".
#
# Notation: a segment covers rows r0..r1; with i = row - r0, g = i // G and k = i % G (G = None:
# g = 0, k = i), the cell in column c (0..7) holds fuse  base + g + rstride*k + T[c].
# An "explicit" segment lists the eight fuse indexes of a single row directly. Cells not covered
# by any segment hold a constant 1. A few fuses appear in two cells; a few appear in none.
#
# In SVF order, the bit shifted first into a word is column 7, so svf_col = 7 - column.

from bitarray import bitarray

from .device import ATF15xxDevice


__all__ = ['ATF15xxBEInstr', 'ATF15xxBEDevice', 'ATF1502BEDevice', 'ATF1504BEDevice', 'ATF1508BEDevice', 'ATF1508REDevice']


class ATF15xxBEInstr:
    IDCODE              = 0x059
    ISC_READ_UES        = 0x270
    ISC_ENABLE          = 0x2a4
    ISC_DISABLE         = 0x2a5
    ISC_FLASH_KEY       = 0x1b9 # DR value shifted after ISC_ENABLE
    ISC_SELECT_BANK1    = 0x290
    ISC_SELECT_BANK2    = 0x294 # ATF1508BE/RE words 4812 and above
    ISC_HANDSHAKE       = 0x291
    ISC_CONTROL         = 0x292 # 8-bit control byte; 0x06/0x02 before a write, 0x03 before a read
    ISC_BULK_ERASE      = 0x293
    ISC_ADDRESS         = 0x2a1 # 16-bit word address, MSB first
    ISC_PAGE_DATA       = 0x2a2 # 256 bits = 32 words
    ISC_WORD_DATA       = 0x2a3 # 8 bits = 1 word
    ISC_PROGRAM         = 0x2a6


_LIN = (7, 6, 5, 4, 3, 2, 1, 0)
_G96 = (672, 576, 480, 384, 288, 192, 96, 0)
_T216A = (189, 2268, 2349, 108, 81, 2160, 2241, 0)
_T216B = (2268, 135, 108, 2295, 2160, 27, 0, 2187)


class ATF15xxBEDevice(ATF15xxDevice):
    word_count = None # int
    segments = None   # list of (r0, r1, base, G, rstride, T) or (r0, 'explicit', cells)
    # programming ranges, bounds inclusive: [(bank, last_page, first_word, last_word)]
    program_ranges = None
    bank2_offset = 4812

    _cells = None     # per class: list of 8-tuples of JED indexes (None = constant 1)
    _inverse = None   # per class: dict jed_index -> [(row, svf_col)]

    @classmethod
    def _build(cls):
        if cls.__dict__.get('_cells') is not None:
            return
        cells = [(None,) * 8 for _ in range(cls.word_count)]
        for seg in cls.segments:
            if seg[1] == 'explicit':
                cells[seg[0]] = tuple(seg[2])
                continue
            r0, r1, base, group, rstride, table = seg
            for row in range(r0, r1 + 1):
                i = row - r0
                g, k = (i // group, i % group) if group else (0, i)
                cells[row] = tuple(base + g + rstride * k + table[c] for c in range(8))
        inverse = {}
        for row, row_cells in enumerate(cells):
            for column, jed_index in enumerate(row_cells):
                if jed_index is not None:
                    inverse.setdefault(jed_index, []).append((row, 7 - column))
        cls._cells, cls._inverse = cells, inverse

    @classmethod
    def word_size(cls, svf_row):
        return 8

    @classmethod
    def jed_to_svf_coords(cls, jed_index):
        cls._build()
        coords = cls._inverse.get(jed_index)
        return coords[0] if coords else None

    @classmethod
    def jed_to_svf(cls, jed_bits):
        cls._build()
        svf_bits = {}
        for row, row_cells in enumerate(cls._cells):
            word = bitarray(8, endian='little')
            word.setall(1)
            for column, jed_index in enumerate(row_cells):
                if jed_index is not None:
                    word[7 - column] = jed_bits[jed_index]
            svf_bits[row] = word
        return svf_bits

    @classmethod
    def svf_to_jed_coords(cls, svf_row, svf_col):
        cls._build()
        return cls._cells[svf_row][7 - svf_col]


class ATF1502BEDevice(ATF15xxBEDevice):
    idcode = 0x0156203f
    fuse_count = 16814
    word_count = 2090
    segments = [
        (   0,   53, 15360, None,   8, _LIN),
        (  54,  107, 15840, None,   8, _LIN),
        ( 108, 1067,     0,   10, 768, _G96),
        (1068, 1117, 16320,   10,  40, (35, 30, 25, 20, 15, 10, 5, 0)),
        (1118, 2077,  7680,   10, 768, _G96),
        (2078, 'explicit', (16745, 16740, 16735, 16730, 16725, 16720, 16720, 16724)),
        (2079, 2082, 16721, None,   1, (25, 20, 15, 10, 5, 0, 0, 0)),
        (2083, 2089, 16750, None,   8, _LIN),
    ]
    program_ranges = [(1, 0x40, 0x81e, 0x829)]


class ATF1504BEDevice(ATF15xxBEDevice):
    idcode = 0x0154403f
    fuse_count = 34198
    word_count = 4252
    segments = [
        (   0,   53, 30720, None,   8, _LIN),
        (  54,  107, 31200, None,   8, _LIN),
        ( 108,  161, 31680, None,   8, _LIN),
        ( 162,  215, 32160, None,   8, _LIN),
        ( 216, 1175,     0,   10, 768, _G96),
        (1176, 1265, 32640,   10,  72, (63, 54, 45, 36, 27, 18, 9, 0)),
        (1266, 2225, 15360,   10, 768, _G96),
        (2226, 3185,  7680,   10, 768, _G96),
        (3186, 3275, 33360,   10,  72, (63, 54, 45, 36, 27, 18, 9, 0)),
        (3276, 4235, 23040,   10, 768, _G96),
        (4236, 'explicit', (34125, 34116, 34107, 34098, 34089, 34080, 34080, 34088)),
        (4237, 4244, 34081, None,   1, (45, 36, 27, 18, 9, 0, 0, 0)),
        (4245, 4251, 34134, None,   8, _LIN),
    ]
    program_ranges = [(1, 0x83, 0x1080, 0x109b)]


class ATF1508BEDevice(ATF15xxBEDevice):
    idcode = 0x0154803f
    fuse_count = 74146
    word_count = 9226
    segments = [
        (   0,   53, 61440, None,   8, _LIN),
        (  54,  107, 61920, None,   8, _LIN),
        ( 108,  161, 62400, None,   8, _LIN),
        ( 162,  215, 62880, None,   8, _LIN),
        ( 216,  269, 63360, None,   8, _LIN),
        ( 270,  323, 63840, None,   8, _LIN),
        ( 324,  377, 64320, None,   8, _LIN),
        ( 378,  431, 64800, None,   8, _LIN),
        ( 432, 1391,  7680,   10, 768, _G96),
        (1392, 1661, 65280,   10, 216, _T216A),
        (1662, 2621,     0,   10, 768, _G96),
        (2622, 3581, 30720,   10, 768, _G96),
        (3582, 3851, 65307,   10, 216, _T216B),
        (3852, 4811, 38400,   10, 768, _G96),
        (4812, 5771, 15360,   10, 768, _G96),
        (5772, 6041, 69600,   10, 216, _T216A),
        (6042, 7001, 23040,   10, 768, _G96),
        (7002, 7961, 53760,   10, 768, _G96),
        (7962, 8231, 69627,   10, 216, _T216B),
        (8232, 9191, 46080,   10, 768, _G96),
        (9192, 'explicit', (74055, 74028, 74001, 73974, 73947, 73920, 73920, 73946)),
        (9193, 9218, 73921, None,   1, (135, 108, 81, 54, 27, 0, 0, 0)),
        (9219, 9225, 74082, None,   8, _LIN),
    ]
    # bank 2 word addresses are relative to bank2_offset
    program_ranges = [(1, 0x95, 0x12c0, 0x12cb), (2, 0x88, 0x1120, 0x113d)]


class ATF1508REDevice(ATF1508BEDevice):
    idcode = 0x0155803f
