#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_character_cards.py
扫描仓库中所有包含 chr.manifest.json 的目录，并将纯图片与角色数据 JSON 拼接为轻量级角色卡 PNG。
仅处理明确声明 chr.manifest.json 的路径，保障生产仓库中旧角色卡的安全。
"""

import sys
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

import os
import struct
import zlib
import json
import base64
import argparse
from pathlib import Path
from typing import Dict, Any, List

def make_text_chunk(keyword: str, text: str) -> bytes:
    """构造标准 PNG tEXt 数据块"""
    key_bytes = keyword.encode('latin1') + b'\x00'
    text_bytes = text.encode('latin1')
    chunk_data = key_bytes + text_bytes
    length = len(chunk_data)
    chunk_type = b'tEXt'
    crc = zlib.crc32(chunk_type + chunk_data) & 0xffffffff
    return struct.pack('>I', length) + chunk_type + chunk_data + struct.pack('>I', crc)

def stitch_png_chunks(png_bytes: bytes, chunks_to_insert: bytes) -> bytes:
    """
    在 PNG 中插入数据块。
    如果底图已包含旧的 chara 或 ccv3 块，将自动剔除，确保元数据绝对纯净无冗余。
    """
    if png_bytes[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('不是合法的 PNG 文件头')

    pos = 8
    output = bytearray(png_bytes[:8])
    found_iend = False

    while pos < len(png_bytes):
        if pos + 8 > len(png_bytes):
            raise ValueError('PNG 块结构损坏，提前遇到文件结尾')

        length = struct.unpack('>I', png_bytes[pos:pos+4])[0]
        chunk_type = png_bytes[pos+4:pos+8]
        total_chunk_len = 12 + length
        if pos + total_chunk_len > len(png_bytes):
            raise ValueError(f'PNG 块 {chunk_type} 长度异常')

        chunk_full = png_bytes[pos:pos+total_chunk_len]

        if chunk_type == b'IEND':
            # 在 IEND 终止块前插入角色卡元数据
            output.extend(chunks_to_insert)
            output.extend(chunk_full)
            found_iend = True
            break
        elif chunk_type == b'tEXt':
            # 检查是否为旧酒馆数据块，若是则剔除
            chunk_data = png_bytes[pos+8:pos+8+length]
            if chunk_data.startswith(b'chara\x00') or chunk_data.startswith(b'ccv3\x00'):
                pos += total_chunk_len
                continue

        output.extend(chunk_full)
        pos += total_chunk_len

    if not found_iend:
        raise ValueError('PNG 缺少 IEND 终止块')

    return bytes(output)

def build_single_card(avatar_path: Path, json_path: Path, output_path: Path, v3_only: bool = False, force: bool = False) -> bool:
    """
    构建单张角色卡。
    返回 True 表示有文件写入/更新，False 表示内容无变化跳过写入。
    """
    if not avatar_path.exists():
        raise FileNotFoundError(f'底图不存在: {avatar_path}')
    if not json_path.exists():
        raise FileNotFoundError(f'角色 JSON 不存在: {json_path}')

    with open(json_path, 'r', encoding='utf-8') as f:
        card_obj = json.load(f)

    # 极简化压缩 JSON（去除空格换行，保留中文字符不转义以减少体积）
    compact_json = json.dumps(card_obj, ensure_ascii=False, separators=(',', ':'))
    b64_data = base64.b64encode(compact_json.encode('utf-8')).decode('ascii')

    with open(avatar_path, 'rb') as f:
        png_bytes = f.read()

    chunks_to_insert = bytearray()
    if not v3_only:
        chunks_to_insert.extend(make_text_chunk('chara', b64_data))
    chunks_to_insert.extend(make_text_chunk('ccv3', b64_data))

    output_bytes = stitch_png_chunks(png_bytes, bytes(chunks_to_insert))

    # 幂等性检查：若输出已存在且内容完全一致，跳过写盘
    if output_path.exists() and not force:
        with open(output_path, 'rb') as f:
            if f.read() == output_bytes:
                print(f'  [跳过] 内容未变动: {output_path.name}')
                return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(output_bytes)

    orig_png_kb = len(png_bytes) / 1024
    json_kb = json_path.stat().st_size / 1024
    out_kb = len(output_bytes) / 1024
    mode_desc = '仅 CCv3' if v3_only else 'V2 + CCv3 双写'

    print(f'  [构建成功] {output_path.name} ({mode_desc})')
    print(f'     ├─ 底图: {orig_png_kb:.2f} KB ({avatar_path.name})')
    print(f'     ├─ 角色 JSON: {json_kb:.2f} KB ({json_path.name})')
    print(f'     └─ 最终角色卡: {out_kb:.2f} KB -> {output_path}')
    return True

def process_manifest(manifest_path: Path, force: bool = False) -> int:
    """
    处理单个 chr.manifest.json 文件。
    返回更新/构建的文件数量。
    """
    manifest_dir = manifest_path.parent
    print(f'\n正在处理清单: {manifest_path}')

    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest_data = json.load(f)

    # 兼容单条对象配置或多条配置列表
    configs: List[Dict[str, Any]] = manifest_data if isinstance(manifest_data, list) else [manifest_data]
    changed_count = 0

    for cfg in configs:
        avatar_rel = cfg.get('avatar')
        json_rel = cfg.get('json')
        output_rel = cfg.get('output')
        v3_only = cfg.get('v3_only', False)

        if not avatar_rel or not json_rel or not output_rel:
            print(f'  [警告] 清单缺少必要字段 (avatar, json, output): {cfg}')
            continue

        avatar_path = (manifest_dir / avatar_rel).resolve()
        json_path = (manifest_dir / json_rel).resolve()
        output_path = (manifest_dir / output_rel).resolve()

        if build_single_card(avatar_path, json_path, output_path, v3_only=v3_only, force=force):
            changed_count += 1

    return changed_count

def scan_and_build(root_dir: Path, target_manifest: str = None, force: bool = False) -> int:
    """
    扫描目录树中所有 chr.manifest.json 并执行构建。
    """
    if target_manifest:
        path = Path(target_manifest).resolve()
        if not path.exists():
            print(f'指定清单不存在: {path}')
            return 1
        manifests = [path]
    else:
        manifests = list(root_dir.rglob('chr.manifest.json'))

    if not manifests:
        print('未找到任何 chr.manifest.json，无需构建。')
        return 0

    print(f'共发现 {len(manifests)} 个 chr.manifest.json 配置文件：')
    for m in manifests:
        try:
            rel = m.relative_to(root_dir)
            print(f'   - {rel}')
        except Exception:
            print(f'   - {m}')

    total_changed = 0
    for m in manifests:
        try:
            total_changed += process_manifest(m, force=force)
        except Exception as e:
            print(f'处理 {m} 时出错: {e}', file=sys.stderr)
            raise e

    print(f'\n全部构建完成！共有 {total_changed} 个角色卡产物更新。')
    return 0

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='酒馆角色卡自动构建工具（仅针对包含 chr.manifest.json 的目录）')
    parser.add_argument('--root', default='.', help='扫描根目录 (默认当前目录)')
    parser.add_argument('--manifest', default=None, help='仅处理指定的 chr.manifest.json 文件')
    parser.add_argument('--force', action='store_true', help='强制覆盖已有产物，即使内容未变')
    args = parser.parse_args()

    root_path = Path(args.root).resolve()
    exit_code = scan_and_build(root_path, target_manifest=args.manifest, force=args.force)
    sys.exit(exit_code)

