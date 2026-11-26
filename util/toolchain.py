import os, os.path, ntpath
import subprocess
import hashlib
import struct
import pickle
import json
from contextlib import contextmanager
from bitarray import bitarray

from . import root_dir, progress
from .jesd3 import JESD3Parser
from . import tt2


vendor_dir = os.path.join(root_dir, "vendor")
work_dir   = os.path.join(root_dir, "work")
cache_dir  = os.path.join(root_dir, "cache")
has_cache  = os.path.exists(cache_dir)
# The fitter builds distributed with WinCUPL do not include `aprim.lib` and cannot read EDIF;
# with those, Verilog input is translated to TT2 instead (see `util/tt2.py`).
has_aprim  = os.path.exists(os.path.join(vendor_dir, "aprim.lib"))

fitter_env = {
    **os.environ,
    "WINEPREFIX": f"{os.getenv('HOME')}/.wine_atf"
}
subprocess.call([
    "wineserver", "-p"
], env=fitter_env)
fitter_env["FITTERDIR"] = subprocess.check_output([
    "winepath", "-w", vendor_dir
], env=fitter_env, text=True).strip()


class FitterError(Exception):
    pass


def run_uncached(input, pins, device, *, strategy={}, options=[], name="work", format="v"):
    progress(0)

    assert format in ("v", "tt", "edif")
    series, package = device.split("-") # eg ATF1502AS-TQFP44

    with open(os.path.join(work_dir, f"{name}.{format}"), "w") as f:
        f.write(input)

    if format == "v" and has_aprim:
        subprocess.check_call([
            "yosys",
            "-q",
            "-p", f"read_verilog -lib {os.path.join(root_dir, 'cells.v')}",
            "-p", f"read_verilog {name}.v",
            "-p", f"hierarchy",
            "-p", f"write_edif -attrprop {name}.edif",
        ], cwd=work_dir)
        format = "edif"
    elif format == "v":
        subprocess.check_call([
            "yosys",
            "-q",
            "-p", f"read_verilog -lib {os.path.join(root_dir, 'cells.v')}",
            "-p", f"read_verilog {name}.v",
            "-p", f"hierarchy -auto-top",
            "-p", f"write_json {name}.json",
        ], cwd=work_dir)
        with open(os.path.join(work_dir, f"{name}.tt"), "w") as f:
            f.write(tt2.translate_file(os.path.join(work_dir, f"{name}.json"), pins or {}))
        format = "tt"

    if pins is not None:
        with open(os.path.join(work_dir, f"{name}.pin"), "w") as f:
            f.write(f"CHIP \"{name}\" ASSIGNED TO AN {package}\n")
            for net, pin in pins.items():
                f.write(f"{net} : {pin}\n")

    # When the fitter fails (e.g. due to routing congestion), it does not produce a JED file.
    # Make sure there isn't a stale one.
    try:
        os.unlink(os.path.join(work_dir, f"{name}.jed"))
    except FileNotFoundError:
        pass

    strategy = {
        "ifmt": format,
        "optimize": "off",
        "DEBUG": "on",
        "output_fast": "off",
        "JTAG": "off",
        **strategy
    }
    if series == 'ATF1504BE' and 'no_tff' not in strategy:
        strategy['no_tff'] = 'on'
    if not has_aprim and 'gclk_itd' in strategy:
        # The WinCUPL builds spell this option `GCK_ITD`, with the pins numbered from 0.
        value = strategy.pop('gclk_itd')
        strategy['GCK_ITD'] = f"GCK{int(value[4:]) - 1}" if value.startswith('GCLK') else value
    strategy_options = []
    for strategy_option in strategy.items():
        strategy_options += ["-strategy", *strategy_option]
    subprocess.check_output([
        "wine", ntpath.join(fitter_env["FITTERDIR"], f"fit{series[3:7]}.exe"),
        "-silent",
        "-i", f"{name}.{format}",
        "-o", f"{name}.jed",
        "-tech", series,
        "-device", package,
        "-preassign", "keep",
        *options,
        *strategy_options,
    ], cwd=work_dir, env=fitter_env)

    with open(os.path.join(work_dir, f"{name}.fit"), encoding='windows-1252') as f:
        fitter_log = f.read()
        if "Warning : Routing fail" in fitter_log:
            raise FitterError("Routing fail")
        if "Warning : Conflict" in fitter_log:
            raise FitterError("Conflict")

    with open(os.path.join(work_dir, f"{name}.jed"), "rb") as f:
        parser = JESD3Parser(f.read())
        parser.parse()
        return parser.fuse


def run(input, pins, device, *, strategy={}, options=[], name="work", format="v"):
    if not has_cache:
        return run_uncached(input, pins, device, strategy=strategy, options=options,
                            name=name, format=format)

    cache_key = hashlib.sha1(json.dumps({
        'input': input,
        'format': format,
        'pins': None if pins is None else dict(sorted(pins.items())),
        'device': device,
        'strategy': strategy,
        'options': options,
    }).encode('ascii')).hexdigest()

    # quick and dirty caching code; i didn't bother making this properly portable because bitarray
    # will screw it up anyway
    try:
        with open(os.path.join(cache_dir, cache_key), 'rb') as f:
            length, = struct.unpack('i', f.read(struct.calcsize('i')))
            if length != 0:
                fuses = bitarray(endian='little')
                fuses.fromfile(f)
                del fuses[length:]
                return fuses
            else:
                raise pickle.load(f)

    except FileNotFoundError:
        @contextmanager
        def write_atomic():
            with open(os.path.join(cache_dir, cache_key + '.new'), 'wb') as f:
                yield f
            os.rename(os.path.join(cache_dir, cache_key + '.new'),
                      os.path.join(cache_dir, cache_key))

        try:
            fuses = run_uncached(input, pins, device, strategy=strategy, options=options,
                                 name=name, format=format)
            with write_atomic() as f:
                f.write(struct.pack('i', len(fuses)))
                fuses.tofile(f)
            return fuses

        except (subprocess.CalledProcessError, FitterError) as exn:
            with write_atomic() as f:
                f.write(struct.pack('i', 0))
                pickle.dump(exn, f)
            raise


# Number of fitter processes `prefetch` may run at once (default 1: no prefetching).
jobs = int(os.environ.get("PRJBUREAU_JOBS", "1"))


def _prefetch_init():
    # The fitter writes its temporary files into %FITTERDIR% under fixed names, so concurrent
    # fitters must not share it: each worker process gets its own copy of the vendor directory.
    import shutil
    worker_dir = os.path.join(work_dir, f"fitter{os.getpid()}")
    shutil.copytree(vendor_dir, worker_dir, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("*.tmp", "*.TMP", "*.ren", "*.REN"))
    fitter_env["FITTERDIR"] = subprocess.check_output([
        "winepath", "-w", worker_dir
    ], env=fitter_env, text=True).strip()


def _prefetch_one(call):
    input, pins, device, kwargs = call
    try:
        run(input, pins, device, name=f"work{os.getpid()}", **kwargs)
    except (subprocess.CalledProcessError, FitterError):
        pass # cached as well; reported when the fuzzer runs it


def prefetch(calls):
    """Fill the cache for `calls`, a list of `(input, pins, device, kwargs)` arguments of `run`,
    using `PRJBUREAU_JOBS` fitter processes in parallel. Fuzzers whose next runs do not depend on
    each other's results call this before running them one by one as usual, so the results are
    the same as without prefetching."""
    if not has_cache or jobs <= 1 or len(calls) <= 1:
        return
    import multiprocessing
    with multiprocessing.get_context("fork").Pool(jobs, initializer=_prefetch_init) as pool:
        for _ in pool.imap_unordered(_prefetch_one, calls):
            pass

