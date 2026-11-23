"""Prints a .def file (the exported names) of a 64-bit Windows DLL, for Zig's dlltool. Standard library only."""
import struct
import sys
from pathlib import Path


def exports(path):
    data = Path(path).read_bytes()
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    optional = pe + 24
    directories = optional + (112 if struct.unpack_from('<H', data, optional)[0] == 0x20b else 96)
    rva = struct.unpack_from('<I', data, directories)[0]
    count, size = struct.unpack_from('<H', data, pe + 6)[0], struct.unpack_from('<H', data, pe + 20)[0]
    sections = [struct.unpack_from('<8sIIII', data, pe + 24 + size + i * 40) for i in range(count)]

    def offset(address):
        for _, virtual_size, virtual, raw_size, raw in sections:
            if virtual <= address < virtual + max(virtual_size, raw_size):
                return address - virtual + raw
        raise ValueError('address outside the sections')

    table = offset(rva)
    names, at = struct.unpack_from('<I', data, table + 24)[0], offset(struct.unpack_from('<I', data, table + 32)[0])
    result = []
    for i in range(names):
        start = offset(struct.unpack_from('<I', data, at + 4 * i)[0])
        result.append(data[start:data.index(b'\0', start)].decode())
    return result


if __name__ == '__main__':
    print('LIBRARY "%s"\nEXPORTS\n%s' % (Path(sys.argv[1]).name, '\n'.join(exports(sys.argv[1]))))
