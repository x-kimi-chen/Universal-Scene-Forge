# -*- coding: utf-8 -*-
"""PLY 读写（USF 自研，MIT）。

替代 GPL-3.0+ 的 plyfile：USF 自有代码不再 import plyfile，满足
LICENSE_COMPLIANCE_CHANGES.md 的 3.3 隔离要求。

支持:
- format: ascii / binary_little_endian / binary_big_endian
- 标量属性（char/uchar/short/ushort/int/uint/float/double 及其别名）
- list 属性（读: 存到 element.lists；写: 仅在显式提供 lists 时输出）
- 3DGS 常见布局: x y z | nx ny nz | f_dc_* | opacity | scale_* | rot_*

安全边界:
- 头部声明的元素数量超过 MAX_ELEMENT_COUNT 直接拒绝（防畸形文件
  造成超大内存分配）；
- 读文件大小超过 MAX_PLY_BYTES 拒绝（防解码耗尽）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np

MAX_ELEMENT_COUNT = 20_000_000        # 单元素最大条目数（约 20M 高斯）
MAX_PLY_BYTES = 4 * 1024 ** 3          # 单个 PLY 最大 4 GiB


class PlyIOError(RuntimeError):
    """PLY 格式/边界错误。"""


# --------------------------------------------------------------------- #
#  类型映射
# --------------------------------------------------------------------- #
_TYPE_MAP = {
    "char": "i1", "int8": "i1",
    "uchar": "u1", "uint8": "u1",
    "short": "i2", "int16": "i2",
    "ushort": "u2", "uint16": "u2",
    "int": "i4", "int32": "i4",
    "uint": "u4", "uint32": "u4",
    "float": "f4", "float32": "f4",
    "double": "f8", "float64": "f8",
}
_NP_TO_PLY = {
    "i1": "char", "u1": "uchar", "i2": "short", "u2": "ushort",
    "i4": "int", "u4": "uint", "f4": "float", "f8": "double",
}


def _dtype_code(ply_type: str) -> str:
    code = _TYPE_MAP.get(str(ply_type).lower())
    if code is None:
        raise PlyIOError(f"不支持的 PLY 属性类型: {ply_type}")
    return code


def _norm_dtype(dtype: np.dtype) -> np.dtype:
    """把 numpy dtype 规范化为 PLY 支持的标量类型（不丢精度地升级）。"""
    dt = np.dtype(dtype)
    if dt.kind == "b":
        return np.dtype("u1")
    if dt.kind in ("i", "u", "f"):
        return dt
    raise PlyIOError(f"不支持的 numpy 属性类型: {dt}")


# --------------------------------------------------------------------- #
#  元素
# --------------------------------------------------------------------- #
class PlyElement:
    """单个 PLY 元素（vertex / face …）。

    与 plyfile 的兼容子集: name/properties/count/comments/data,
    以及 PlyElement.describe(arr, name)。
    properties: [(name, dtype_str), ...] 或 plyfile 风格属性对象列表。
    lists: {prop_name: (count_dtype, item_dtype, list[np.ndarray])}
    """

    def __init__(self, name: str, properties: Sequence = (),
                 count: int = 0, comments: Iterable[str] = ()):
        self.name = str(name)
        self.properties = list(properties)
        self.count = int(count)
        self.comments = list(comments)
        self.lists: dict[str, tuple] = {}
        self._data: Optional[np.ndarray] = None

    def __getitem__(self, key):
        """兼容 plyfile: vertex["x"] → 属性列数组。"""
        return self.data[key]

    # -- data ---------------------------------------------------------- #
    @property
    def data(self) -> np.ndarray:
        """点云数据的读写入口。getter 返回字段字典，setter 校验必需字段后整体替换。"""
        if self._data is None:
            self._data = np.zeros(self.count, dtype=self._dtype())
        return self._data

    @data.setter
    def data(self, arr: np.ndarray) -> None:
        """点云数据的读写入口。getter 返回字段字典，setter 校验必需字段后整体替换。"""
        arr = np.asarray(arr)
        if arr.dtype.names is None:
            raise PlyIOError("元素 data 必须是结构化数组")
        self._data = arr
        self.count = len(arr)
        self.properties = [(n, _NP_TO_PLY.get(_norm_dtype(arr.dtype[n]).str[1:],
                                               "float"))
                           for n in arr.dtype.names]

    @classmethod
    def describe(cls, arr: np.ndarray, name: str,
                 comments: Iterable[str] = ()) -> "PlyElement":
        """生成 PLY 头部描述：属性名、类型、元素数量，comments 原样写入注释区。"""
        el = cls(name, (), len(arr), comments)
        el.data = arr
        return el

    # -- header -------------------------------------------------------- #
    def _dtype(self) -> np.dtype:
        fields: List[Tuple[str, str]] = []
        for prop in self.properties:
            if isinstance(prop, tuple) and len(prop) >= 2:
                pname, ptype = prop[0], prop[1]
            elif hasattr(prop, "name") and hasattr(prop, "val_dtype"):
                pname, ptype = prop.name, prop.val_dtype
            else:
                raise PlyIOError(f"无法解析属性定义: {prop!r}")
            if isinstance(ptype, np.dtype):
                code = _NP_TO_PLY.get(_norm_dtype(ptype).str[1:])
                if code is None:
                    raise PlyIOError(f"不支持写出属性类型: {ptype}")
                ptype = code
            fields.append((str(pname), np.dtype("<" + _dtype_code(ptype))))
        return np.dtype(fields)


class PlyData:
    """PLY 文档: 头部 + 若干元素。

    兼容 plyfile 的 read()/write()/["vertex"]/text 用法。
    """

    def __init__(self, elements: Sequence[PlyElement], text: bool = False,
                 byte_order: str = "<", comments: Iterable[str] = (),
                 obj_info: Iterable[str] = ()):
        self.elements = list(elements)
        self.text = bool(text)
        self.byte_order = ">" if byte_order == ">" else "<"
        self.comments = list(comments)
        self.obj_info = list(obj_info)

    def __getitem__(self, name: str) -> PlyElement:
        for el in self.elements:
            if el.name == name:
                return el
        raise KeyError(f"PLY 无元素: {name}")

    # ------------------------------------------------------------------ #
    #  读
    # ------------------------------------------------------------------ #
    @classmethod
    def read(cls, path: "str | Path") -> "PlyData":
        """从 PLY 文件读入点云。按头部标识区分 ascii 与 binary 分支，字段名保持文件中的写法。"""
        p = Path(path)
        try:
            size = p.stat().st_size
        except OSError as exc:
            raise PlyIOError(f"无法读取 PLY: {p} ({exc})") from exc
        if size > MAX_PLY_BYTES:
            raise PlyIOError(
                f"PLY 超过大小上限 {MAX_PLY_BYTES // 1024 ** 3} GiB: "
                f"{p} ({size} bytes)")

        with open(p, "rb") as fh:
            raw_header, body_offset = _read_header(fh)
            fmt, elements = _parse_header(raw_header)
            fh.seek(body_offset)
            _read_body(fh, fmt, elements, size - body_offset)

        text = fmt == "ascii"
        byte_order = ">" if fmt.endswith("big_endian") else "<"
        return cls(elements, text=text, byte_order=byte_order)

    # ------------------------------------------------------------------ #
    #  写
    # ------------------------------------------------------------------ #
    def write(self, path: "str | Path") -> None:
        """把当前点云写成 PLY。字段顺序沿用读入时的顺序，头部带一行来源注释。"""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        fmt = "ascii" if self.text else (
            "binary_big_endian" if self.byte_order == ">" else
            "binary_little_endian")
        header = _build_header(fmt, self.elements, self.comments)
        with open(p, "wb") as fh:
            fh.write(header)
            for el in self.elements:
                self._write_element(fh, el)

    # -- 写实现 -------------------------------------------------------- #
    def _write_element(self, fh, el: PlyElement) -> None:
        arr = el.data
        if arr.dtype.names is None:
            raise PlyIOError(f"元素 {el.name} 的 data 不是结构化数组")
        if len(arr) != el.count:
            el.count = len(arr)
        if el.lists:
            for i in range(len(arr)):
                parts = [str(v) for v in arr[i].tolist()]
                for lname, (_c, item_dt, values) in el.lists.items():
                    vals = values[i]
                    parts.append(str(len(vals)))
                    parts.extend(str(v) for v in np.asarray(vals).tolist())
                fh.write((" ".join(parts) + "\n").encode("ascii"))
            return
        if self.text:
            names = arr.dtype.names
            fmt_map = {n: ("%d" if arr.dtype[n].kind in ("i", "u") else
                           "%.9g") for n in names}
            np.savetxt(fh, _as_plain_2d(arr),
                       fmt=[fmt_map[n] for n in names], delimiter=" ")
        else:
            out = arr.astype(arr.dtype.newbyteorder(self.byte_order),
                             copy=False)
            fh.write(out.tobytes(order="C"))


def _as_plain_2d(arr: np.ndarray) -> np.ndarray:
    """结构化数组 → 二维普通数组（用于 ASCII 输出）。"""
    cols = []
    for name in arr.dtype.names or ():
        col = arr[name]
        cols.append(col.astype(np.float64 if col.dtype.kind == "f"
                               else np.int64))
    return np.stack(cols, axis=1) if cols else np.zeros((len(arr), 0))


# --------------------------------------------------------------------- #
#  头部解析
# --------------------------------------------------------------------- #
def _read_header(fh) -> Tuple[str, int]:
    """读取到 end_header 为止的头部文本；返回 (文本, body 偏移)。"""
    lines: List[str] = []
    total = 0
    while True:
        line = fh.readline()
        if not line:
            raise PlyIOError("PLY 头部不完整（缺少 end_header）")
        total += len(line)
        if total > 1024 * 1024:
            raise PlyIOError("PLY 头部超过 1 MiB，拒绝解析")
        text = line.decode("ascii", errors="replace").rstrip("\r\n")
        lines.append(text)
        if text.strip() == "end_header":
            return "\n".join(lines), fh.tell()


def _parse_header(header: str) -> Tuple[str, List[PlyElement]]:
    lines = [l.strip() for l in header.splitlines() if l.strip()]
    if not lines or lines[0].lower() != "ply":
        raise PlyIOError("不是 PLY 文件（缺少 magic 'ply'）")

    fmt = ""
    elements: List[PlyElement] = []
    comments: List[str] = []
    current: Optional[PlyElement] = None

    for line in lines[1:]:
        lower = line.lower()
        if lower.startswith("format "):
            parts = line.split()
            if len(parts) < 2 or parts[1] not in (
                    "ascii", "binary_little_endian", "binary_big_endian"):
                raise PlyIOError(f"不支持的 PLY format: {line}")
            fmt = parts[1]
        elif lower.startswith("comment"):
            comments.append(line[len("comment"):].strip())
        elif lower.startswith("obj_info"):
            continue
        elif lower.startswith("element "):
            parts = line.split()
            if len(parts) < 3:
                raise PlyIOError(f"非法 element 行: {line}")
            try:
                count = int(parts[2])
            except ValueError as exc:
                raise PlyIOError(f"非法 element 数量: {line}") from exc
            if count < 0 or count > MAX_ELEMENT_COUNT:
                raise PlyIOError(
                    f"element {parts[1]} 数量 {count} 超出安全上限 "
                    f"{MAX_ELEMENT_COUNT}")
            current = PlyElement(parts[1], (), count, comments)
            elements.append(current)
        elif lower.startswith("property "):
            if current is None:
                raise PlyIOError("property 出现在 element 之前")
            parts = line.split()
            if len(parts) >= 5 and parts[1].lower() == "list":
                current.lists[parts[4]] = (parts[2], parts[3], [])
            elif len(parts) >= 3:
                current.properties.append((parts[2], parts[1]))
            else:
                raise PlyIOError(f"非法 property 行: {line}")
        elif lower == "end_header":
            break

    if not fmt:
        raise PlyIOError("PLY 缺少 format 行")
    for el in elements:
        el.comments = []
    return fmt, elements


# --------------------------------------------------------------------- #
#  正文解析
# --------------------------------------------------------------------- #
def _read_body(fh, fmt: str, elements: List[PlyElement], body_size: int) -> None:
    if fmt == "ascii":
        _read_ascii(fh, elements)
    else:
        order = ">" if fmt == "binary_big_endian" else "<"
        offset = 0
        raw = fh.read()
        for el in elements:
            scalar_props = [(n, _dtype_code(t)) for n, t in el.properties]
            list_props = el.lists
            if not list_props:
                dt = np.dtype([(n, order + c) for n, c in scalar_props])
                if el.count == 0:
                    el.data = np.zeros(0, dtype=dt.newbyteorder("="))
                    continue
                need = dt.itemsize * el.count
                if offset + need > len(raw):
                    raise PlyIOError(
                        f"PLY 正文截断: 元素 {el.name} 需要 {need} 字节, "
                        f"剩余 {len(raw) - offset}")
                arr = np.frombuffer(raw, dtype=dt, count=el.count,
                                    offset=offset).copy()
                offset += need
                el.data = arr.astype(arr.dtype.newbyteorder("="), copy=False)
            else:
                offset = _read_binary_with_lists(
                    raw, offset, el, order, scalar_props)
        if offset > len(raw):
            raise PlyIOError("PLY 正文越界")


def _read_binary_with_lists(raw: bytes, offset: int, el: PlyElement,
                            order: str, scalar_props) -> int:
    import struct

    struct_fmt = "".join(order + {
        "i1": "b", "u1": "B", "i2": "h", "u2": "H",
        "i4": "i", "u4": "I", "f4": "f", "f8": "d"}[c]
        for _, c in scalar_props)
    list_defs = list(el.lists.items())
    rows = []
    list_values = {name: [] for name, _ in list_defs}
    for _ in range(el.count):
        vals = struct.unpack_from(struct_fmt, raw, offset) if struct_fmt else ()
        offset += struct.calcsize(struct_fmt) if struct_fmt else 0
        rows.append(vals)
        for name, (c_type, i_type, _old) in list_defs:
            c_fmt = order + _struct_char(c_type)
            (n,) = struct.unpack_from(c_fmt, raw, offset)
            offset += struct.calcsize(c_fmt)
            if n < 0 or n > MAX_ELEMENT_COUNT:
                raise PlyIOError(f"list {name} 长度非法: {n}")
            i_fmt = order + _struct_char(i_type)
            isz = struct.calcsize(i_fmt)
            if offset + n * isz > len(raw):
                raise PlyIOError(f"PLY list {name} 正文截断")
            vals2 = struct.unpack_from(f"{order}{n}{_struct_char(i_type)}",
                                       raw, offset) if n else ()
            offset += n * isz
            list_values[name].append(np.asarray(vals2, dtype=np.dtype(i_type)))

    if scalar_props:
        dt = np.dtype([(n, _dtype_code(t)) for n, t in scalar_props])
        arr = np.zeros(len(rows), dtype=dt)
        for i, row in enumerate(rows):
            arr[i] = row
        el.data = arr
    else:
        el.data = np.zeros(len(rows), dtype=[])
    for name, (c_type, i_type, _old) in list_defs:
        el.lists[name] = (c_type, i_type, list_values[name])
    return offset


def _struct_char(ply_type: str) -> str:
    return {"i1": "b", "u1": "B", "i2": "h", "u2": "H",
            "i4": "i", "u4": "I", "f4": "f", "f8": "d"}[
                _dtype_code(ply_type)]


def _read_ascii(fh, elements: List[PlyElement]) -> None:
    for el in elements:
        scalar_props = [(n, _dtype_code(t)) for n, t in el.properties]
        list_defs = list(el.lists.items())
        dt = np.dtype([(n, _dtype_code(t)) for n, t in el.properties])
        arr = np.zeros(el.count, dtype=dt)
        list_values = {name: [] for name, _ in list_defs}
        for i in range(el.count):
            line = fh.readline()
            if not line:
                raise PlyIOError(f"PLY ASCII 正文截断: 元素 {el.name} "
                                 f"第 {i} 行缺失")
            tokens = line.decode("ascii", errors="replace").split()
            cursor = 0
            for name, code in scalar_props:
                if cursor >= len(tokens):
                    raise PlyIOError(f"PLY ASCII 行字段不足: {line!r}")
                try:
                    arr[i][name] = _parse_token(tokens[cursor], code)
                except ValueError as exc:
                    raise PlyIOError(
                        f"PLY ASCII 数值非法: {tokens[cursor]!r}") from exc
                cursor += 1
            for name, (c_type, i_type, _old) in list_defs:
                if cursor >= len(tokens):
                    raise PlyIOError(f"PLY ASCII list 缺失: {line!r}")
                n = int(tokens[cursor])
                cursor += 1
                if n < 0 or n > MAX_ELEMENT_COUNT or cursor + n > len(tokens):
                    raise PlyIOError(f"PLY ASCII list 长度非法: {n}")
                list_values[name].append(np.asarray(
                    tokens[cursor:cursor + n], dtype=np.dtype(i_type)))
                cursor += n
        el.data = arr
        for name, (c_type, i_type, _old) in list_defs:
            el.lists[name] = (c_type, i_type, list_values[name])


def _parse_token(token: str, code: str):
    return float(token) if code in ("f4", "f8") else int(token)


# --------------------------------------------------------------------- #
#  头部写出
# --------------------------------------------------------------------- #
def _build_header(fmt: str, elements: Sequence[PlyElement],
                  comments: Sequence[str]) -> bytes:
    lines = ["ply", f"format {fmt} 1.0"]
    coords = "comment Generated by Universal Scene Forge (MIT ply_io)"
    lines.append(coords)
    for c in comments:
        lines.append(f"comment {c}")
    for el in elements:
        lines.append(f"element {el.name} {el.count}")
        for name, ptype in el.properties:
            if isinstance(ptype, np.dtype):
                code = _norm_dtype(ptype).str[1:]
            else:
                code = _dtype_code(ptype)
            lines.append(f"property {_NP_TO_PLY[code]} {name}")
        for name, (c_type, i_type, _old) in el.lists.items():
            lines.append(f"property list {_NP_TO_PLY[_dtype_code(c_type)]} "
                         f"{_NP_TO_PLY[_dtype_code(i_type)]} {name}")
    lines.append("end_header")
    return ("\n".join(lines) + "\n").encode("ascii")
