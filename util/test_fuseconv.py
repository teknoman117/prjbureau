import io
import random
import unittest
from bitarray import bitarray

from .fuseconv import DEVICES, read_svf, write_svf


class FuseconvRoundTripTestCase(unittest.TestCase):
    """JED -> SVF -> JED must be lossless for every fuse that is stored in the device."""

    def roundtrip(self, device_name):
        device = DEVICES[device_name]
        rng = random.Random(device_name)
        jed_bits = bitarray(device.fuse_count, endian='little')
        for index in range(device.fuse_count):
            jed_bits[index] = rng.getrandbits(1)
        svf_bits = device.jed_to_svf(jed_bits)
        svf_file = io.StringIO()
        write_svf(svf_file, svf_bits, device, comment=device_name)
        read_bits, _ = read_svf(io.StringIO(svf_file.getvalue()), device)
        self.assertEqual({row: word.to01() for row, word in read_bits.items()},
                         {row: word.to01() for row, word in svf_bits.items()})
        back = device.svf_to_jed(read_bits)
        for index in range(device.fuse_count):
            if device.jed_to_svf_coords(index) is not None:
                self.assertEqual(back[index], jed_bits[index], f"{device_name} fuse {index}")

    def test_atf1502as(self): self.roundtrip('ATF1502AS')
    def test_atf1504as(self): self.roundtrip('ATF1504AS')
    def test_atf1508as(self): self.roundtrip('ATF1508AS')
    def test_atf1502be(self): self.roundtrip('ATF1502BE')
    def test_atf1504be(self): self.roundtrip('ATF1504BE')
    def test_atf1508be(self): self.roundtrip('ATF1508BE')
    def test_atf1508re(self): self.roundtrip('ATF1508RE')


class BEPackingTestCase(unittest.TestCase):
    def test_coverage(self):
        # every word cell is either a JED fuse or a constant 1, and every JED fuse that is
        # stored at all is stored in a cell that maps back to it
        for name in ('ATF1502BE', 'ATF1504BE', 'ATF1508BE'):
            device = DEVICES[name]
            for index in range(device.fuse_count):
                coords = device.jed_to_svf_coords(index)
                if coords is not None:
                    self.assertEqual(device.svf_to_jed_coords(*coords), index)


if __name__ == '__main__':
    unittest.main()
