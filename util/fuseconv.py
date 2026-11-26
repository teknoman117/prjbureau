import argparse
import textwrap
from bitarray import bitarray

from .jesd3 import JESD3Parser
from .svf import SVFParser, SVFEventHandler
from .device import *
from .device_be import *


def read_jed(file):
    parser = JESD3Parser(file.read().encode('ascii'))
    parser.parse()
    return parser.fuse, parser.design_spec.decode('ascii')


def write_jed(file, jed_bits, *, comment):
    assert '*' not in comment
    file.write("\x02{}*\n".format(comment))
    file.write("QF{}* F0*\n".format(len(jed_bits)))
    chunk_size = 64
    for start in range(0, len(jed_bits), chunk_size):
        file.write("L{:05d} {}*\n".format(start, jed_bits[start:start+chunk_size].to01()))
    file.write("\x030000\n")


class ATFSVFEventHandler(SVFEventHandler):
    def ignored(self, *args, **kwargs):
        pass
    svf_frequency = ignored
    svf_trst = ignored
    svf_state = ignored
    svf_endir = ignored
    svf_enddr = ignored
    svf_hir = ignored
    svf_sir = ignored
    svf_tir = ignored
    svf_hdr = ignored
    svf_sdr = ignored
    svf_tdr = ignored
    svf_runtest = ignored
    svf_piomap = ignored
    svf_pio = ignored

    def __init__(self):
        self.ir = None
        self.erase = False
        self.addr = 0
        self.data = b''
        self.bits = {}

    def svf_sir(self, tdi, smask, tdo, mask):
        self.ir = int.from_bytes(tdi.tobytes(), 'little')
        if self.ir == ATF15xxInstr.ISC_LATCH_ERASE:
            self.erase = True
        if self.ir == ATF15xxInstr.ISC_DATA:
            self.erase = False

    def svf_sdr(self, tdi, smask, tdo, mask):
        if self.ir == ATF15xxInstr.ISC_ADDRESS:
            self.addr = int.from_bytes(tdi.tobytes(), 'little')
        if (self.ir & ~0x3) == ATF15xxInstr.ISC_DATA:
            self.data = tdi

    def svf_runtest(self, run_state, run_count, run_clock, min_time, max_time, end_state):
        if not self.erase and self.ir == ATF15xxInstr.ISC_PROGRAM_ERASE:
            self.bits[self.addr] = self.data


class ATFBESVFEventHandler(ATFSVFEventHandler):
    """Simulates the BE-series write path: [bank select] [address] [page/word data] [program]."""

    def __init__(self, device):
        super().__init__()
        self.device = device
        self.bank_offset = 0
        self.pending = None

    def svf_sir(self, tdi, smask, tdo, mask):
        self.ir = int.from_bytes(tdi.tobytes(), 'little')
        if self.ir == ATF15xxBEInstr.ISC_SELECT_BANK1:
            self.bank_offset = 0
        elif self.ir == ATF15xxBEInstr.ISC_SELECT_BANK2:
            self.bank_offset = self.device.bank2_offset
        elif self.ir == ATF15xxBEInstr.ISC_PROGRAM and self.pending is not None:
            for index, word in enumerate(self.pending):
                self.bits[self.bank_offset + self.addr + index] = word
            self.pending = None

    def svf_sdr(self, tdi, smask, tdo, mask):
        if self.ir == ATF15xxBEInstr.ISC_ADDRESS:
            # the 16-bit address is shifted MSB first
            self.addr = int(tdi.to01(), 2)
        elif self.ir in (ATF15xxBEInstr.ISC_PAGE_DATA, ATF15xxBEInstr.ISC_WORD_DATA):
            if tdo is None: # verify scans carry TDO; only writes are recorded
                self.pending = [tdi[i:i + 8] for i in range(0, len(tdi), 8)]


def read_svf(file, device=None):
    if device is not None and issubclass(device, ATF15xxBEDevice):
        handler = ATFBESVFEventHandler(device)
        parser = SVFParser(file.read(), handler)
        parser.parse_file()
        return handler.bits, ''
    handler = ATFSVFEventHandler()
    parser = SVFParser(file.read(), handler)
    parser.parse_file()
    return handler.bits, ''


def _bitarray_to_hex(input_bits):
    bits = bitarray(input_bits, endian="little")
    bits.bytereverse()
    bits.reverse()
    return bits.tobytes().hex()


def write_svf_be(file, svf_bits, device, *, comment):
    """BE-series programming sequence, following the one written by Atmel ATMISP 7.3 (see the
    open-atf150x specification, chapter 7.5): bulk erase, then every word written through the
    32-word page buffer and the trailing words one at a time, then verified."""
    I = ATF15xxBEInstr
    w = file.write
    def sir(op):
        w("SIR 10 TDI ({:03x});\n".format(op))
    def sdr(bits, tdo=False):
        value = int(bits.to01()[::-1], 2)
        digits = (len(bits) + 3) // 4
        if tdo:
            w("SDR {} TDI ({:0{}x})\n\tTDO ({:0{}x})\n\tMASK ({:0{}x});\n".format(
                len(bits), value, digits, value, digits, (1 << len(bits)) - 1, digits))
        else:
            w("SDR {} TDI ({:0{}x});\n".format(len(bits), value, digits))
    def byte(value):
        return bitarray(format(value, '08b')[::-1], endian='little')
    def addr16(value):
        return bitarray(format(value, '016b'), endian='little') # MSB first
    def idle():
        w("STATE IDLE;\n")
    def wait_us(us):
        w("RUNTEST IDLE {}E-6 SEC;\n".format(us))
    def clocks(n):
        w("RUNTEST {} TCK;\n".format(n))
    def hs(sel):
        sir(sel); idle(); wait_us(1)
    def words(offset, first, count):
        out = bitarray(endian='little')
        for index in range(first, first + count):
            out += svf_bits[offset + index]
        return out
    def write(sel, address, data_ir, data, page):
        hs(sel)
        sir(I.ISC_CONTROL); sdr(byte(0x06)); idle(); clocks(8); clocks(10)
        hs(I.ISC_HANDSHAKE)
        hs(sel)
        sir(I.ISC_CONTROL); sdr(byte(0x02)); idle(); clocks(8); clocks(5)
        sir(I.ISC_ADDRESS); sdr(addr16(address)); idle(); clocks(16)
        sir(data_ir); sdr(data); idle(); clocks(256 if page else 8)
        sir(I.ISC_PROGRAM); idle(); wait_us(10001)
        hs(I.ISC_HANDSHAKE)
    def read(sel, address, data_ir, data):
        hs(sel)
        sir(I.ISC_CONTROL); sdr(byte(0x03)); idle(); clocks(8)
        sir(I.ISC_ADDRESS); sdr(addr16(address)); idle(); clocks(16)
        sir(data_ir); sdr(data, tdo=True)
        idle(); wait_us(1); hs(I.ISC_HANDSHAKE)
    def enable():
        sir(I.ISC_ENABLE); sdr(bitarray(format(I.ISC_FLASH_KEY, '010b')[::-1], endian='little')); idle()
    def disable():
        sir(I.ISC_DISABLE); sdr(bitarray('0' * 10)); idle(); wait_us(10001)

    for comment_line in comment.splitlines():
        w("// {}\n".format(comment_line))
    for line in ("TRST ABSENT;", "ENDIR IDLE;", "ENDDR IDLE;", "HDR 0;", "HIR 0;", "TDR 0;", "TIR 0;",
                 "STATE RESET;"):
        w(line + "\n")
    w("// Check IDCODE\n")
    sir(I.IDCODE)
    w("SDR 32 TDI (ffffffff)\n\tTDO ({:08x})\n\tMASK (ffffffff);\n".format(device.idcode))
    w("// ISC enable, bulk erase\n")
    enable()
    sir(I.ISC_BULK_ERASE); idle(); wait_us(1800001)
    disable()
    enable()
    for verify in (False, True):
        w("// ISC {} words\n".format("verify" if verify else "program"))
        for bank, last_page, first_word, last_word in device.program_ranges:
            sel = I.ISC_SELECT_BANK1 if bank == 1 else I.ISC_SELECT_BANK2
            offset = 0 if bank == 1 else device.bank2_offset
            for page in range(last_page + 1):
                data = words(offset, 32 * page, 32)
                if verify: read(sel, 32 * page, I.ISC_PAGE_DATA, data)
                else:      write(sel, 32 * page, I.ISC_PAGE_DATA, data, True)
            for word in range(first_word, last_word + 1):
                data = words(offset, word, 1)
                if verify: read(sel, word, I.ISC_WORD_DATA, data)
                else:      write(sel, word, I.ISC_WORD_DATA, data, False)
    w("// ISC disable\n")
    disable()


def write_svf(file, svf_bits, device, *, comment):
    if issubclass(device, ATF15xxBEDevice):
        return write_svf_be(file, svf_bits, device, comment=comment)
    # This code is kind of awful.
    def emit_header():
        for comment_line in comment.splitlines():
            file.write("// {}\n".format(comment_line))
        file.write("TRST ABSENT;\n")
        file.write("ENDIR IDLE;\n")
        file.write("ENDDR IDLE;\n")
        file.write("HDR 0;\n")
        file.write("HIR 0;\n")
        file.write("TDR 0;\n")
        file.write("TIR 0;\n")
        file.write("STATE RESET;\n")
    def emit_check_idcode(idcode):
        file.write("// Check IDCODE\n")
        file.write("SIR 10 TDI ({:03x});\n".format(ATF15xxInstr.IDCODE))
        file.write("SDR 32 TDI (ffffffff)\n\tTDO ({:08x})\n\tMASK (ffffffff);\n".format(idcode))
    def emit_enable():
        file.write("// ISC enable\n")
        file.write("SIR 10 TDI ({:03x});\n".format(ATF15xxInstr.ISC_CONFIG))
        file.write("SDR 10 TDI ({:03x});\n".format(0x1b9)) # magic constant?
        file.write("STATE IDLE;\n")
    def emit_disable():
        file.write("// ISC disable\n")
        file.write("SIR 10 TDI ({:03x});\n".format(ATF15xxInstr.ISC_CONFIG))
        file.write("SDR 10 TDI ({:03x});\n".format(0x000))
        file.write("STATE IDLE;\n")
    def emit_unknown():
        # ATMISP does this for unknown reasons. DR seems to be just BYPASS. Removing this
        # doesn't do anything (and shouldn't do anything, since ATMISP doesn't go through RTI
        # or capture/update DR), but let's keep it for now. Vendor tools wouldn't emit SIR
        # without any reason whatsoever, right? Right??
        file.write("// ISC unknown\n")
        file.write("SIR 10 TDI ({:03x});\n".format(ATF15xxInstr.ISC_UNKNOWN))
    def emit_erase():
        file.write("// ISC erase\n")
        file.write("SIR 10 TDI ({:03x});\n".format(ATF15xxInstr.ISC_LATCH_ERASE))
        file.write("SIR 10 TDI ({:03x});\n".format(ATF15xxInstr.ISC_PROGRAM_ERASE))
        file.write("RUNTEST IDLE 210E-3 SEC;\n")
        emit_unknown()
    def emit_program(address, data):
        file.write("// ISC program word\n")
        file.write("SIR 10 TDI ({:03x});\n".format(ATF15xxInstr.ISC_ADDRESS))
        file.write("SDR 11 TDI ({:03x});\n".format(address))
        file.write("SIR 10 TDI ({:03x});\n".format(ATF15xxInstr.ISC_DATA | (address >> 8)))
        file.write("SDR {} TDI ({:0{}x});\n".format(len(data),
            int(data.to01()[::-1], 2), len(data) // 4))
        file.write("SIR 10 TDI ({:03x});\n".format(ATF15xxInstr.ISC_PROGRAM_ERASE))
        file.write("RUNTEST IDLE 30E-3 SEC;\n")
        emit_unknown()
    def emit_verify(address, data):
        file.write("// ISC verify word\n")
        file.write("SIR 10 TDI ({:03x});\n".format(ATF15xxInstr.ISC_ADDRESS))
        file.write("SDR 11 TDI ({:03x});\n".format(address))
        file.write("SIR 10 TDI ({:03x});\n".format(ATF15xxInstr.ISC_READ))
        file.write("RUNTEST IDLE 20E-3 SEC;\n")
        file.write("SIR 10 TDI ({:03x});\n".format(ATF15xxInstr.ISC_DATA | (address >> 8)))
        file.write("SDR {} TDI ({:0{}x})\n\tTDO ({:0{}x})\n\tMASK ({:0{}x});\n".format(len(data),
            int(data.to01()[::-1], 2), len(data) // 4,
            int(data.to01()[::-1], 2), len(data) // 4,
            (1 << len(data)) - 1, len(data) // 4))

    emit_header()
    emit_check_idcode(device.idcode)
    emit_enable()
    emit_erase()
    for svf_row in svf_bits:
        emit_program(svf_row, svf_bits[svf_row])
    for svf_row in svf_bits:
        emit_verify(svf_row, svf_bits[svf_row])
    emit_disable()


DEVICES = {
    'ATF1502AS': ATF1502ASDevice,
    'ATF1504AS': ATF1504ASDevice,
    'ATF1508AS': ATF1508ASDevice,
    'ATF1502BE': ATF1502BEDevice,
    'ATF1504BE': ATF1504BEDevice,
    'ATF1508BE': ATF1508BEDevice,
    'ATF1508RE': ATF1508REDevice,
}


class ATFFileType(argparse.FileType):
    def __call__(self, value):
        file = super().__call__(value)
        filename = file.name.lower()
        if not (filename.endswith('.jed') or filename.endswith('.svf')):
            raise argparse.ArgumentTypeError('{} is not a JED or SVF file'.format(filename))
        return file


def arg_parser():
    parser = argparse.ArgumentParser(description=textwrap.dedent("""
    Convert between ATF15xx JED and SVF files. The type of the file is determined by the extension
    (``.jed`` or ``.svf``, respectively).

    If an SVF file is provided as an input, it is used to drive the JTAG programming state machine
    of a simulated device. The state machine is greatly simplified and only functions correctly
    when driven with vectors that program every non-reserved bit at least once.
    """))
    parser.add_argument(
        '-d', '--device', metavar='DEVICE',
        choices=tuple(DEVICES), default='ATF1502AS',
        help='Select the device to use.')
    parser.add_argument(
        'input', metavar='INPUT', type=ATFFileType('r'),
        help='Read fuses from file INPUT.')
    parser.add_argument(
        'output', metavar='OUTPUT', type=ATFFileType('w'),
        help='Write fuses to file OUTPUT.')
    return parser


def main():
    args = arg_parser().parse_args()

    device = DEVICES[args.device]

    jed_bits = svf_bits = None
    if args.input.name.lower().endswith('.jed'):
        jed_bits, comment = read_jed(args.input)
        if device.fuse_count != len(jed_bits):
            raise SystemExit(f"Device has {device.fuse_count} fuses, JED file "
                             f"has {len(jed_bits)}; wrong --device option?")
    elif args.input.name.lower().endswith('.svf'):
        svf_bits, comment = read_svf(args.input, device)
    else:
        assert False

    if args.output.name.lower().endswith('.jed'):
        if jed_bits is None:
            jed_bits = device.svf_to_jed(svf_bits)
        write_jed(args.output, jed_bits, comment=comment)
    elif args.output.name.lower().endswith('.svf'):
        if svf_bits is None:
            svf_bits = device.jed_to_svf(jed_bits)
        write_svf(args.output, svf_bits, device, comment=comment)
    else:
        assert False


if __name__ == '__main__':
    main()
